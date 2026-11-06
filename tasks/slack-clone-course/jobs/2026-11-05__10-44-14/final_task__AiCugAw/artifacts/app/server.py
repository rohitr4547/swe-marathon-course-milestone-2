import argparse, asyncio, json, re, os, sys, secrets
from zoneinfo import ZoneInfo
from aiohttp import web, WSMsgType
import core
NODE=0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(request):
    try:
        x=await request.json()
        return x if isinstance(x,dict) else None
    except: return None
def require_user(request):
    h=request.headers.get('Authorization','')
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized()
    return u
def out(d,status=200): return web.json_response(d,status=status)
def rid(v):
    try:return int(v)
    except: return None
def role(w,u): return core.member_role(w,u)
def require_member(w,u):
    r=role(w,u)
    if not r: raise web.HTTPForbidden()
    return r
def channel_access(u,ch):
    if not ch: raise web.HTTPNotFound()
    return core.can_read_channel(u['id'],ch)
async def health(request): return out({'status':'ok','node_id':NODE})
async def register(request):
    b=await read_json(request)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')): return json_error(400,'invalid username')
    if not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid password')
    if 'email' in b:return json_error(400,'email is not accepted')
    with core.tx() as c:
        try:
            cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
        except: return json_error(409,'username exists')
        u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone();t=core.issue_token(c,u['id'])
    return out({'user':core.user_to_json(u),'token':t},201)
async def login(request):
    b=await read_json(request)
    if not b or not isinstance(b.get('username'),str) or not isinstance(b.get('password'),str):return json_error(401,'invalid credentials')
    c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone()
    if not u or not core.verify_password(b['password'],u['password']):c.close();return json_error(401,'invalid credentials')
    t=core.issue_token(c,u['id']);c.close();return out({'user':core.user_to_json(u),'token':t})
async def me(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    return out({'user':core.user_to_json(u)})
async def profile(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    b=await read_json(request)
    if not b:return json_error(400,'invalid body')
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if any(k not in allowed or not isinstance(v,str) or len(v)>250 for k,v in b.items()):return json_error(400,'invalid profile')
    if 'timezone' in b:
        try: ZoneInfo(b['timezone'])
        except: return json_error(400,'invalid timezone')
    limits={'display_name':80,'status_text':140,'status_emoji':32,'avatar_url':512}
    if any(k in b and len(b[k])==0 or (k in b and len(b[k])>n) for k,n in limits.items()):return json_error(400,'invalid profile')
    with core.tx() as c:
        if b:
            c.execute('UPDATE users SET '+','.join(k+'=?' for k in b)+' WHERE id=?',(*b.values(),u['id']))
            core.emit_global(c,{'type':'user.updated','user':core.user_to_json(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())})
        u=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
    return out({'user':core.user_to_json(u)})
async def users(request):
    try:require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();u=c.execute('SELECT * FROM users WHERE id=?',(rid(request.match_info['id']),)).fetchone();c.close()
    return out({'user':core.user_to_json(u)}) if u else json_error(404,'not found')
async def workspaces(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return out({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_workspace(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    b=await read_json(request)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')) or not isinstance(b.get('name'),str) or not b['name']:return json_error(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return json_error(409,'duplicate slug')
        cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']));w=cur.lastrowid
        core.add_workspace_member(c,w,u['id'],'owner');ch=core.create_channel_row(c,w,'general')
        core.add_channel_member(c,ch,u['id']);wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone();cr=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
    return out({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(cr)},201)
def state_for(uid,chid):
    c=core.connect()
    row=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(uid,chid)).fetchone()
    last=row['last_read_event_id'] if row else 0
    ev=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(chid,last)).fetchall()
    unread=mentions=0
    for e in ev:
        try:
            x=json.loads(e['payload']); m=x.get('message') or {}
            if m.get('author_id')!=uid and x.get('type') in ('message.created','message.reply'):
                unread+=1
                if uid in (m.get('mentions') or []): mentions+=1
        except: pass
    c.close()
    return {'channel_id':chid,'last_read_event_id':last,'unread_count':unread,'mention_count':mentions}
async def workspace(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],request.query.get('include_archived') in ('1','true'))).fetchall()
    rr=role(w['id'],u['id'])
    rs=[x for x in rs if not x['is_private'] or rr in ('owner','admin') or core.can_read_channel(u['id'],x)]
    c.close()
    return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rs],'read_state':[state_for(u['id'],x['id']) for x in rs]})
async def members(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT user_id,role FROM memberships WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
    return out({'members':[dict(x) for x in rs]})
async def patch_workspace(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    b=await read_json(request) or {}
    if 'name' in b and not isinstance(b['name'],str):return json_error(400,'invalid name')
    if 'join_mode' in b and (not isinstance(b['join_mode'],str) or b['join_mode'] not in ('open','invite_only')):return json_error(400,'invalid join mode')
    vals={k:b[k] for k in ('name','join_mode') if k in b}
    if not vals:return json_error(400,'invalid body')
    with core.tx() as c:
        c.execute('UPDATE workspaces SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),w['id']))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return out({'workspace':core.workspace_to_json(w)})
async def create_channel(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    rr=role(w['id'],u['id'])
    if rr not in ('owner','admin','member'):return json_error(403,'forbidden')
    b=await read_json(request)
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return json_error(400,'invalid channel')
    private=bool(b.get('is_private',False))
    if private and rr not in ('owner','admin'):return json_error(403,'forbidden')
    if 'topic' in b and (not isinstance(b['topic'],str) or len(b['topic'])>250):return json_error(400,'invalid topic')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return json_error(409,'duplicate channel')
        i=core.create_channel_row(c,w['id'],b['name'],private,b.get('topic',''));core.add_channel_member(c,i,u['id']);r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
    return out({'channel':core.channel_to_json(r)},201)
async def join(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if ch['is_private']:return json_error(403,'private')
    w=core.get_workspace(core.connect().execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()['slug']) if ch['workspace_id'] else None
    if w and role(w['id'],u['id'])=='guest':return json_error(403,'guest cannot join')
    if w and not role(w['id'],u['id']):
        if w['join_mode']!='open':return json_error(403,'invite required')
        with core.tx() as c:core.add_workspace_member(c,w['id'],u['id'])
    with core.tx() as c:
        already=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        if not already:
            core.add_channel_member(c,ch['id'],u['id'])
            core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
    return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def channel_members(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    return out({'members':[core.user_to_json(x) for x in rs]})
async def messages(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    try:n,cur=core.parse_paging(request.query,'messages')
    except:return json_error(400,'invalid pagination')
    try:rs,nc=core.list_messages(ch['id'],n,cur)
    except:return json_error(400,'invalid pagination')
    return out({'messages':rs,'next_cursor':nc})
async def post_message(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    b=await read_json(request)
    if ch['is_archived'] and not (isinstance(b,dict) and isinstance(b.get('body'),str) and b['body'].strip().lower()=='/unarchive'):return json_error(423,'archived')
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty body')
    if 'parent_id' in b and (not isinstance(b['parent_id'],int) or isinstance(b['parent_id'],bool)):return json_error(400,'invalid parent')
    file_ids=b.get('file_ids',[])
    if not isinstance(file_ids,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in file_ids) or len(set(file_ids))!=len(file_ids):return json_error(400,'invalid files')
    if file_ids:
        c=core.connect()
        for fid in file_ids:
            f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not f or f['uploader_id']!=u['id'] or f['message_id'] is not None:
                c.close();return json_error(400,'invalid file attachment')
        c.close()
    body=b['body']
    if body.startswith('//'):
        body=body[1:]
    elif body.startswith('/'):
        parts=body.split(None,1)
        cmd=parts[0][1:]
        arg=parts[1] if len(parts)>1 else ''
        if cmd=='me':
            if not arg.strip():return json_error(400,'/me requires text')
            body='/me '+arg
        elif cmd=='shrug':
            body=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
        elif cmd=='topic':
            if not arg.strip():return json_error(400,'topic requires text')
            if not ch['workspace_id'] or not channel_access(u,ch):return json_error(403,'forbidden')
            if len(arg)>250:return json_error(400,'invalid topic')
            with core.tx() as c:
                c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']))
                rr=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'], )).fetchone()
                core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(rr)})
            return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
        elif cmd=='invite':
            target=arg.strip()
            if not target.startswith('@') or not ch['workspace_id'] or not channel_access(u,ch):return json_error(403 if not channel_access(u,ch) else 400,'forbidden')
            c=core.connect();tu=c.execute('SELECT * FROM users WHERE username=?',(target[1:],)).fetchone();c.close()
            if not tu:return json_error(400,'unknown user')
            with core.tx() as c:
                core.add_channel_member(c,ch['id'],tu['id'])
            return out({'message':None,'invited_user':core.user_to_json(tu)},201)
        elif cmd in ('archive','unarchive'):
            if not ch['workspace_id'] or role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
            archived=1 if cmd=='archive' else 0
            with core.tx() as c:
                c.execute('UPDATE channels SET is_archived=? WHERE id=?',(archived,ch['id']))
                rr=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'], )).fetchone()
                core.emit_event(c,ch['id'],'channel.'+('archived' if archived else 'unarchived'),{'channel':core.channel_to_json(rr)})
            return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
        else:
            return json_error(400,'unknown command')
    try:m=core.create_message(u,ch['id'],body,b.get('parent_id'))
    except ValueError as e:return json_error(423 if str(e)=='archived' else 400,str(e))
    if file_ids:
        with core.tx() as c:
            for fid in file_ids:c.execute('UPDATE files SET message_id=? WHERE id=?',(m['id'],fid))
        c=core.connect();row=c.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone();c.close();m=core.message_to_json(row)
    return out({'message':m},201)
async def replies(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();p=c.execute('SELECT * FROM messages WHERE id=?',(rid(request.match_info['id']),)).fetchone();c.close()
    if not p:return json_error(404,'not found')
    ch=core.get_channel(p['channel_id'])
    if not channel_access(u,ch):return json_error(403,'forbidden')
    try:n,cur=core.parse_paging(request.query,'replies')
    except:return json_error(400,'invalid pagination')
    rs,nc=core.list_replies(p['id'],n,cur)
    return out({'replies':rs,'next_cursor':nc})
async def edit_message(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    mid=rid(request.match_info['id']);b=await read_json(request)
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'invalid body')
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    if m['deleted']:return json_error(409,'message deleted')
    if m['author_id']!=u['id']:return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],core.now_iso(),mid));m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        core.emit_event(c,m['channel_id'],'message.updated',{'message':core.message_to_json(m)})
    return out({'message':core.message_to_json(m)})
async def delete_message(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    mid=rid(request.match_info['id']);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    if m['deleted']:return out({'deleted':True})
    ch=core.get_channel(m['channel_id']);rr=role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
    if m['author_id']!=u['id'] and rr!='owner':return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
    return out({'deleted':True})
async def reaction(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    mid=rid(request.match_info['id']);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not channel_access(u,ch):return json_error(403,'forbidden')
    emoji=request.match_info.get('emoji')
    if request.method=='POST':
        b=await read_json(request)
        if not b or not isinstance(b.get('emoji'),str) or not b['emoji']:return json_error(400,'invalid emoji')
        emoji=b['emoji']
        with core.tx() as c:
            cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
            if cur.rowcount:
                core.emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    else:
        with core.tx() as c:
            cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
            if cur.rowcount:
                core.emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    return out({'reactions':core.reactions_for(mid)})
async def patch_channel(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if ch['is_archived']:return json_error(423,'archived')
    explicit=core.can_read_channel(u['id'],ch) and (not ch['workspace_id'] or core.connect().execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone())
    owner_ok=False
    if ch['workspace_id']:
        c0=core.connect();owner_ok=bool(c0.execute('SELECT 1 FROM workspaces WHERE id=? AND owner_id=?',(ch['workspace_id'],u['id'])).fetchone());c0.close()
    if not explicit and not owner_ok:return json_error(403,'forbidden')
    b=await read_json(request) or {}
    if not isinstance(b.get('topic'),str) or len(b['topic'])>250:return json_error(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']));r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone();core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(r)})
    return out({'channel':core.channel_to_json(r)})
async def role_patch(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    rr=role(w['id'],u['id']);target=rid(request.match_info['user_id']);b=await read_json(request)
    c=core.connect();tm=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone();c.close()
    if not tm:return json_error(404,'not found')
    if not b or b.get('role') not in ('owner','admin','member','guest') or b['role']=='owner':return json_error(400,'invalid role')
    if rr not in ('owner','admin') or target==u['id'] or (rr=='admin' and tm['role'] in ('admin','owner')):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?',(b['role'],w['id'],target))
    return out({'member':{'user_id':target,'role':b['role']}})
async def transfer(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if w['owner_id']!=u['id']:return json_error(403,'forbidden')
    b=await read_json(request) or {};c=core.connect();t=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],b.get('new_owner_id'))).fetchone();c.close()
    if not t or t['role']!='admin':return json_error(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE memberships SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id']));c.execute('UPDATE memberships SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],b['new_owner_id']));c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(b['new_owner_id'],w['id']))
    return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def ws_handler(request):
    t=request.query.get('token','');u=core.user_for_token(t)
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse();await ws.prepare(request);conn_id=secrets.token_urlsafe(12);core.touch_presence(conn_id,u['id']) if hasattr(core,'touch_presence') else None;subs={};last={};global_last=0
    async def deliver():
        nonlocal global_last
        while not ws.closed:
            for ch,s in list(subs.items()):
                for ev in core.events_after(ch,s):
                    await ws.send_json(ev);subs[ch]=ev['event_id']
            for gid,ev in core.globals_after(global_last):
                await ws.send_json(ev);global_last=gid
            core.touch_presence(conn_id,u['id']) if hasattr(core,'touch_presence') else None
            await asyncio.sleep(.05)
    task=asyncio.create_task(deliver())
    try:
        async for msg in ws:
            if msg.type==WSMsgType.TEXT:
                try:b=json.loads(msg.data)
                except:continue
                if b.get('type')=='subscribe':
                    ch=int(b['channel_id']);head=core.head_event_id(ch);subs[ch]=head;await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
                elif b.get('type')=='resume':
                    ch=int(b['channel_id']);since=int(b.get('since_event_id',0));subs[ch]=since
                    for ev in core.events_after(ch,since):await ws.send_json(ev);subs[ch]=ev['event_id']
                    await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
    finally:
        task.cancel()
        if hasattr(core,'drop_presence'): core.drop_presence(conn_id)
    return ws
async def leave(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch) and not (ch['workspace_id'] and role(ch['workspace_id'],u['id'])):return json_error(403,'forbidden')
    with core.tx() as c:
        existed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        if existed:
            core.remove_channel_member(c,ch['id'],u['id'])
            core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def archive(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not ch['workspace_id'] or role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=1 WHERE id=?',(ch['id'],))
        r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.archived',{'channel':core.channel_to_json(r)})
    return out({'channel':core.channel_to_json(r)})
async def unarchive(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not ch['workspace_id'] or role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=0 WHERE id=?',(ch['id'],))
        r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.unarchived',{'channel':core.channel_to_json(r)})
    return out({'channel':core.channel_to_json(r)})
async def search(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    q=request.query.get('q')
    if not isinstance(q,str):return json_error(400,'invalid query')
    try:n,cur=core.parse_paging(request.query,'search')
    except:return json_error(400,'invalid pagination')
    c=core.connect()
    wid=request.query.get('workspace_id')
    if wid is not None:
        try: wid=int(wid)
        except: c.close(); return json_error(400,'invalid workspace_id')
        rows=c.execute('SELECT m.*,ch.workspace_id FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.deleted=0 AND ch.workspace_id=? AND m.body LIKE ? AND m.id<? ORDER BY m.id DESC LIMIT ?', (wid,'%'+q+'%',cur or 10**18,n+1)).fetchall()
    else:
        rows=c.execute('SELECT m.*,ch.workspace_id FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.deleted=0 AND m.body LIKE ? AND m.id<? ORDER BY m.id DESC LIMIT ?',('%'+q+'%',cur or 10**18,n+1)).fetchall()
    visible=[]
    for x in rows:
        if core.can_read_channel(u['id'],x) if False else True:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(x['channel_id'],)).fetchone()
            if core.can_read_channel(u['id'],ch): visible.append(x)
    c.close()
    more=len(visible)>n;visible=visible[:n]
    return out({'results':[core.message_to_json(x) for x in visible],'next_cursor':core.encode_cursor('search',visible[-1]['id']) if more and visible else None})


async def groups(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    rr=role(w['id'],u['id'])
    if not rr:return json_error(403,'forbidden')
    handle=request.match_info.get('handle')
    if request.method=='GET':
        c=core.connect();rs=c.execute('SELECT * FROM groups_ WHERE workspace_id=? ORDER BY id',(w['id'],)).fetchall();result=[]
        for g in rs:
            ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]
            result.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids})
        c.close();return out({'groups':result})
    if request.method=='POST':
        b=await read_json(request) or {}; users=b.get('user_ids',[])
        if not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(users,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in users):return json_error(400,'invalid group')
        c=core.connect();dups=c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],b['handle'])).fetchone();bad=[x for x in users if not c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone()];c.close()
        if dups:return json_error(409,'duplicate handle')
        if bad:return json_error(400,'non-member')
        with core.tx() as c:
            gid=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],b['handle'],b['name'],u['id'])).lastrowid
            for uid in users:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,uid))
        return out({'group':{'id':gid,'handle':b['handle'],'name':b['name'],'member_user_ids':users}},201)
    c=core.connect();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone();c.close()
    if not g:
        return out({'deleted':True}) if request.method=='DELETE' else json_error(404,'not found')
    if request.method=='DELETE':
        if g['creator_id']!=u['id'] and rr!='owner':return json_error(403,'forbidden')
        with core.tx() as c:c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],))
        return out({'deleted':True})
    if request.method=='PATCH':
        if g['creator_id']!=u['id'] and rr!='owner':return json_error(403,'forbidden')
        b=await read_json(request) or {}
        if 'user_ids' in b:
            users=b['user_ids']
        else:
            c0=core.connect();users=[x[0] for x in c0.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall()];c0.close()
        if not isinstance(b.get('name',g['name']),str) or not isinstance(users,list):return json_error(400,'invalid group')
        c=core.connect();bad=[x for x in users if not isinstance(x,int) or not c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone()];c.close()
        if bad:return json_error(400,'non-member')
        with core.tx() as c:
            c.execute('UPDATE groups_ SET name=? WHERE id=?',(b.get('name',g['name']),g['id']));c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
            for uid in users:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],uid))
        return out({'group':{'id':g['id'],'handle':g['handle'],'name':b.get('name',g['name']),'member_user_ids':users}})
    return json_error(400,'unsupported')
async def dms(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    b=await read_json(request)
    if not b or not isinstance(b.get('user_id'),int) or isinstance(b.get('user_id'),bool):return json_error(400,'invalid user_id')
    other=b['user_id']
    c=core.connect();target=c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone();c.close()
    if not target:return json_error(404,'unknown user')
    if other==u['id']:return json_error(400,'cannot dm self')
    a,bid=sorted((u['id'],other))
    with core.tx() as c:
        old=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bid)).fetchone()
        if old: cid=old['channel_id']
        else:
            cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',('',1));cid=cur.lastrowid
            c.execute('INSERT INTO dms VALUES(?,?,?)',(a,bid,cid));c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,bid))
    return out({'channel_id':cid})
async def upload_file(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    try:
        f=await request.multipart();part=await f.next()
        if not part or part.name!='file':return json_error(400,'file required')
        data=await part.read()
    except web.HTTPRequestEntityTooLarge:return json_error(413,'file too large')
    except:return json_error(400,'invalid upload')
    if len(data)>10*1024*1024:return json_error(413,'file too large')
    with core.tx() as c:
        cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],part.filename or 'file',part.headers.get('Content-Type') or 'application/octet-stream',len(data),core.now_iso()))
        fid=cur.lastrowid
    os.makedirs('/app/data/files',exist_ok=True)
    open('/app/data/files/'+str(fid),'wb').write(data)
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    return out({'file':file_json(r)},201)
def file_json(r):
    return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
async def get_file(request):
    try:require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(rid(request.match_info['id']),)).fetchone();c.close()
    return out({'file':file_json(r)}) if r else json_error(404,'not found')
async def download_file(request):
    try:require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(rid(request.match_info['id']),)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    return web.FileResponse('/app/data/files/'+str(r['id']),headers={'Content-Type':r['content_type'],'Content-Disposition':'attachment; filename="'+r['filename']+'"'})

async def pins(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    try:n,cur=core.parse_paging(request.query,'pins')
    except:return json_error(400,'invalid pagination')
    c=core.connect()
    rows=c.execute('SELECT p.message_id,p.pinned_by,p.pinned_at,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND p.message_id<? ORDER BY p.message_id DESC LIMIT ?', (ch['id'],cur or 10**18,n+1)).fetchall()
    c.close()
    more=len(rows)>n;rows=rows[:n]
    return out({'pins':[{'message':core.message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows],
                'next_cursor':core.encode_cursor('pins',rows[-1]['message_id']) if more and rows else None})
async def pin(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    mid=rid(request.match_info['id']);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()));p=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    return out({'pin':dict(p)})
async def unpin(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    mid=rid(request.match_info['id'])
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
    return out({'unpinned':True})
async def read_state(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(rid(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    if request.method=='POST':
        b=await read_json(request)
        if not b or not isinstance(b.get('last_read_event_id'),int) or isinstance(b.get('last_read_event_id'),bool):return json_error(400,'invalid read state')
        c=core.connect();old=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone();prev=old['last_read_event_id'] if old else 0;c.close()
        n=max(prev,b['last_read_event_id'])
        with core.tx() as c:c.execute('INSERT INTO read_state(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],ch['id'],n))
    return out({'read_state':state_for(u['id'],ch['id'])})
async def create_invite(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    b=await read_json(request)
    if b is None:return json_error(400,'invalid body')
    for k in ('expires_in_seconds','max_uses','invited_user_id'):
        if k in b and not isinstance(b[k],int):return json_error(400,'invalid invitation')
    if 'expires_in_seconds' in b and b['expires_in_seconds']<=0:return json_error(400,'invalid expiration')
    if 'max_uses' in b and b['max_uses']<1:return json_error(400,'invalid max uses')
    if 'name' in b and not isinstance(b['name'],str):return json_error(400,'invalid name')
    if 'invited_username' in b and not isinstance(b['invited_username'],str):return json_error(400,'invalid target')
    target=b.get('invited_user_id')
    if target is not None and not isinstance(target,int):return json_error(400,'invalid target')
    if b.get('invited_username'):
        c=core.connect();z=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone();c.close()
        if not z:return json_error(404,'unknown user')
        target=z['id']
    import time
    code=secrets.token_urlsafe(10); expires=int(b.get('expires_in_seconds',604800)); maximum=1 if target is not None else int(b.get('max_uses',1))
    with core.tx() as c:
        c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?)',(code,w['id'],u['id'],time.time()+expires,maximum,0,target))
    inv={'code':code,'workspace_id':w['id'],'created_by':u['id'],'max_uses':maximum,'uses':0,'invited_user_id':target}
    return out({'invitation':inv},201)
async def list_invitations(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    import time
    c=core.connect();rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND expires_at>? AND uses<max_uses ORDER BY code',(w['id'],time.time())).fetchall();c.close()
    return out({'invitations':[{'code':x['code'],'workspace_id':x['workspace_id'],'created_by':x['creator_id'],'max_uses':x['max_uses'],'uses':x['uses'],'invited_user_id':x['invited_user_id']} for x in rs]})
async def revoke_invite(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    code=request.match_info['code'];c=core.connect();x=c.execute('SELECT 1 FROM invitations WHERE code=? AND workspace_id=?',(code,w['id'])).fetchone();c.close()
    if not x:return json_error(404,'not found')
    with core.tx() as c:c.execute('DELETE FROM invitations WHERE code=? AND workspace_id=?',(code,w['id']))
    return out({'deleted':True})
async def accept_invite(request):
    try:u=require_user(request)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    code=request.match_info['code'];c=core.connect();i=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone();c.close()
    if not i or i['expires_at']<__import__('time').time() or i['uses']>=i['max_uses']:return json_error(404,'not found')
    if i['invited_user_id'] and i['invited_user_id']!=u['id']:return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,));core.add_workspace_member(c,i['workspace_id'],u['id'])
    return out({'accepted':True})
app=web.Application(client_max_size=11*1024*1024)
app.router.add_get('/api/health',health)
app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_patch('/api/users/me',profile);app.router.add_get('/api/users/{id}',users)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_workspace);app.router.add_post('/api/dms',dms);app.router.add_get('/api/workspaces/{slug}/groups',groups);app.router.add_post('/api/workspaces/{slug}/groups',groups);app.router.add_patch('/api/workspaces/{slug}/groups/{handle}',groups);app.router.add_delete('/api/workspaces/{slug}/groups/{handle}',groups);app.router.add_post('/api/files',upload_file);app.router.add_get('/api/files/{id}',get_file);app.router.add_get('/api/files/{id}/download',download_file)
app.router.add_get('/api/workspaces/{slug}',workspace);app.router.add_get('/api/workspaces/{slug}/members',members);app.router.add_patch('/api/workspaces/{slug}',patch_workspace)
app.router.add_post('/api/workspaces/{slug}/channels',create_channel);app.router.add_patch('/api/workspaces/{slug}/members/{user_id}',role_patch);app.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)
app.router.add_post('/api/workspaces/{slug}/invitations',create_invite);app.router.add_get('/api/workspaces/{slug}/invitations',list_invitations);app.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_invite);app.router.add_post('/api/invitations/{code}/accept',accept_invite)
app.router.add_post('/api/channels/{id}/join',join);app.router.add_post('/api/channels/{id}/leave',leave);app.router.add_post('/api/channels/{id}/archive',archive);app.router.add_post('/api/channels/{id}/unarchive',unarchive);app.router.add_get('/api/channels/{id}/members',channel_members);app.router.add_patch('/api/channels/{id}',patch_channel)
app.router.add_get('/api/channels/{id}/messages',messages);app.router.add_post('/api/channels/{id}/messages',post_message);app.router.add_get('/api/search',search);app.router.add_get('/api/messages/{id}/replies',replies)
app.router.add_patch('/api/messages/{id}',edit_message);app.router.add_delete('/api/messages/{id}',delete_message)
app.router.add_post('/api/messages/{id}/reactions',reaction);app.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction)
app.router.add_get('/api/channels/{id}/pins',pins);app.router.add_post('/api/messages/{id}/pin',pin);app.router.add_delete('/api/messages/{id}/pin',unpin)
app.router.add_get('/api/channels/{id}/read',read_state);app.router.add_post('/api/channels/{id}/read',read_state)
app.router.add_get('/api/ws',ws_handler)
app.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'))
if __name__=='__main__':
    core.init_db();NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0;p=8000+NODE
    web.run_app(app,host='127.0.0.1',port=p)
