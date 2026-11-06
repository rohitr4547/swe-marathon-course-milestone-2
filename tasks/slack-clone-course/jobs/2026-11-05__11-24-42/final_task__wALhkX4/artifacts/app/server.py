import argparse, asyncio, json, re, os
from aiohttp import web, WSMsgType
import core
NODE=0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(req):
    try:
        x=await req.json()
        return x if isinstance(x,dict) else None
    except: return None
def require_user(req):
    h=req.headers.get('Authorization','')
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized()
    return u
def user_or_error(req):
    try:return require_user(req)
    except web.HTTPException as e: raise
def out(x,status=200): return web.json_response(x,status=status)
def channel_access(u,ch,write=False):
    c=core.connect(); m=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
    role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None; c.close()
    return bool(m) or (not ch['is_private'] and role in ('owner','admin','member'))
def file_list(mid):
    c=core.connect(); rows=c.execute('SELECT * FROM files WHERE message_id=? ORDER BY id',(mid,)).fetchall(); c.close()
    return [file_obj(x) for x in rows]
def msg_json(r):
    c=core.connect(); a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    cnt=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':core.user_to_json(a),
      'body':r['body'],'parent_id':r['parent_id'],'reply_count':cnt,'created_at':r['created_at'],'edited_at':r['edited_at'],
      'files':file_list(r['id']),'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs],
      'mentions':json.loads(r['mentions'] or '[]')}
async def get_user(req):
    require_user(req)
    c=core.connect(); r=c.execute('SELECT * FROM users WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    return out({'user':core.user_to_json(r)})
async def update_profile(req):
    u=require_user(req); d=await read_json(req)
    allowed={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512)}
    if d is None or any(k not in allowed and k!='timezone' for k in d):return json_error(400,'invalid profile')
    for k,(lo,hi) in allowed.items():
        if k in d and (not isinstance(d[k],str) or len(d[k])<lo or len(d[k])>hi):return json_error(400,'invalid profile')
    if 'timezone' in d:
        if not isinstance(d['timezone'],str):return json_error(400,'invalid timezone')
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(d['timezone'])
        except: return json_error(400,'invalid timezone')
    with core.tx() as c:
        for k,v in d.items(): c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
        r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        core.emit_global(c,{'type':'user.updated','user':core.user_to_json(r)})
    return out({'user':core.user_to_json(r)})
async def index(req):
    return web.FileResponse('/app/static/index.html')
async def health(req): return out({'status':'ok','node_id':NODE})
async def register(req):
    d=await read_json(req)
    if not d or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d['username']) or not isinstance(d.get('password'),str) or len(d['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(d['username'],)).fetchone(): return json_error(409,'username exists')
        c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username'],core.now_iso()))
        u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
    return out({'user':core.user_to_json(u),'token':t},201)
async def login(req):
    d=await read_json(req); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if d else None,)).fetchone(); c.close()
    if not u or not isinstance(d.get('password') if d else None,str) or not core.verify_password(d['password'],u['password']): return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return out({'user':core.user_to_json(u),'token':t})
async def me(req):
    u=require_user(req); return out({'user':core.user_to_json(u)})
async def create_workspace(req):
    u=require_user(req); d=await read_json(req)
    if not d or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d['slug']) or not isinstance(d.get('name'),str) or not d['name']:return json_error(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(d['slug'],)).fetchone():return json_error(409,'slug exists')
        c.execute("INSERT INTO workspaces(slug,name,owner_id,created_at) VALUES(?,?,?,?)",(d['slug'],d['name'],u['id'],core.now_iso()))
        w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone()
        c.execute("INSERT INTO channels(workspace_id,name) VALUES(?, 'general')",(w['id'],)); ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone()
        c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(w['id'],u['id'],'owner')); c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
    return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
async def workspaces(req):
    u=require_user(req); c=core.connect(); a=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close(); return out({'workspaces':[core.workspace_to_json(x) for x in a]})
async def workspace(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); q='SELECT * FROM channels WHERE workspace_id=? AND (? OR is_archived=0) AND (is_private=0 OR id IN (SELECT channel_id FROM channel_members WHERE user_id=?))'; a=c.execute(q,(w['id'],req.query.get('include_archived') in ('1','true'),u['id'])).fetchall(); c.close()
    states=[]
    for x in a:
        rr=await read_state_obj(u['id'],x['id'])
        states.append(rr)
    return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in a],'read_state':states})
async def create_channel(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    d=await read_json(req)
    if not d or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']) or (not isinstance(d.get('topic',''),str)) or len(d.get('topic',''))>250:return json_error(400,'invalid channel')
    private=bool(d.get('is_private',False)); role=core.member_role(w['id'],u['id'])
    if role=='guest':return json_error(403,'forbidden')
    if private and role not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return json_error(409,'duplicate channel')
        c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],private,d.get('topic','')))
        ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone(); c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
    return out({'channel':core.channel_to_json(ch)},201)
async def join(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    w=core.get_workspace(str(ch['workspace_id'])) if False else (core.connect().execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone())
    role=core.member_role(ch['workspace_id'],u['id'])
    c0=core.connect(); cm=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
    if ch['is_private'] and not cm:return json_error(403,'forbidden')
    if role=='guest' and not cm:return json_error(403,'forbidden')
    if not role and w['join_mode']=='invite_only':return json_error(403,'invite required')
    with core.tx() as c:
        if not role:core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
        core.add_channel_member(c,ch['id'],u['id'])
    return out({'channel':core.channel_to_json(ch)})
async def channel_members(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    c=core.connect(); a=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close(); return out({'members':[core.user_to_json(x) for x in a]})
async def messages(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    c=core.connect(); a=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC LIMIT 200',(ch['id'],)).fetchall(); c.close(); return out({'messages':[msg_json(x) for x in a],'next_cursor':None})
def resolve_mentions(c,body,ch,author):
    out=[]
    if re.search(r'(?<![\w@.])@channel\b',body):
        rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],author)).fetchall()
        out.extend(x['user_id'] for x in rows)
    names=re.findall(r'(?<![\w@.])@(\w+)',body)
    if ch['workspace_id']:
        for name in names:
            if name not in ('channel','here'):
                x=c.execute('SELECT u.id FROM users u JOIN workspace_members wm ON wm.user_id=u.id WHERE wm.workspace_id=? AND u.username=?',(ch['workspace_id'],name)).fetchone()
                if x and x['id']!=author: out.append(x['id'])
        handles=re.findall(r'(?<![\w@.])@([a-z0-9-]{1,32})',body)
        for handle in handles:
            g=c.execute('SELECT id FROM groups WHERE workspace_id=? AND handle=?',(ch['workspace_id'],handle)).fetchone()
            if g:
                rows=c.execute('SELECT gm.user_id FROM group_members gm JOIN channel_members cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=? AND gm.user_id!=?',(g['id'],ch['id'],author)).fetchall()
                out.extend(x['user_id'] for x in rows)
        if re.search(r'(?<![\w@.])@here\b',body):
            rows=c.execute('SELECT DISTINCT cm.user_id FROM channel_members cm JOIN presence p ON p.user_id=cm.user_id WHERE cm.channel_id=? AND cm.user_id!=? AND p.last_seen>?',(ch['id'],author,__import__('time').time()-3)).fetchall()
            out.extend(x['user_id'] for x in rows)
    return list(dict.fromkeys(x for x in out if x!=author))

async def slash(user,ch,body):
    if body.startswith('//'): return None,body[1:]
    parts=body.split(None,1); cmd=parts[0]
    arg=parts[1] if len(parts)>1 else ''
    if cmd=='/me':
        if not arg.strip(): return 'error',None
        return None,body
    if cmd=='/shrug':
        return None,(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
    if cmd=='/topic':
        if not arg:return 'error',None
        if core.member_role(ch['workspace_id'],user['id']) is None:return 'forbidden',None
        with core.tx() as c:
            c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']))
            x=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
            core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(x)})
        return {'message':None,'channel':core.channel_to_json(x)},None
    if cmd=='/invite':
        if not re.fullmatch(r'@[A-Za-z0-9_]+',arg.strip()):return 'error',None
        c=core.connect(); x=c.execute('SELECT * FROM users WHERE username=?',(arg.strip()[1:],)).fetchone();c.close()
        if not x:return 'error',None
        if not core.member_role(ch['workspace_id'],user['id']):return 'forbidden',None
        with core.tx() as c:core.add_channel_member(c,ch['id'],x['id'])
        return {'message':None,'invited_user':core.user_to_json(x)},None
    if cmd in ('/archive','/unarchive'):
        w=core.get_workspace(str(ch['workspace_id'])) if False else None
        c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
        if user['id']!=w['owner_id']:return 'forbidden',None
        val=1 if cmd=='/archive' else 0
        with core.tx() as c:
            old=ch['is_archived'];c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
            x=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
            if old!=val:core.emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':core.channel_to_json(x)})
        return {'message':None,'channel':core.channel_to_json(x)},None
    return 'error',None

async def post_message(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    d=await read_json(req)
    if not d or not isinstance(d.get('body'),str):return json_error(400,'invalid body')
    if d['body'].startswith('/') and not d['body'].startswith('//'):
        result,newbody=await slash(u,ch,d['body'])
        if result=='error':return json_error(400,'unknown command')
        if result=='forbidden':return json_error(403,'forbidden')
        if isinstance(result,dict):return out(result,201)
        d['body']=newbody
    elif d['body'].startswith('//'): d['body']=d['body'][1:]
    if ch['is_archived']:return json_error(423,'archived')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    if not d or not isinstance(d.get('body'),str) or not d['body'].strip() or ('parent_id' in d and d['parent_id'] is not None and (not isinstance(d['parent_id'],int) or not core.connect().execute('SELECT 1 FROM messages WHERE id=? AND channel_id=?',(d['parent_id'],ch['id'])).fetchone())):return json_error(400,'invalid body')
    fids=d.get('file_ids',[])
    if not isinstance(fids,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in fids):return json_error(400,'invalid files')
    if len(set(fids))!=len(fids):return json_error(400,'invalid files')
    cf=core.connect()
    for fid in fids:
        fr=cf.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
        if not fr or fr['uploader_id']!=u['id'] or fr['message_id'] is not None:
            cf.close(); return json_error(400,'invalid file')
    cf.close()
    with core.tx() as c:
        mentions=resolve_mentions(c,d['body'],ch,u['id'])
        c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch['id'],u['id'],d['body'],d.get('parent_id'),core.now_iso(),json.dumps(mentions)))
        r=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone();
        for fid in fids:c.execute('UPDATE files SET message_id=? WHERE id=?',(r['id'],fid))
        r=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone(); f=core.emit_event(c,ch['id'],'message.reply' if d.get('parent_id') else 'message.created',{'message':msg_json(r)})
    return out({'message':msg_json(r)},201)
async def edit(req):
    u=require_user(req); mid=int(req.match_info['id']); d=await read_json(req)
    c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    if r['deleted']:return json_error(409,'deleted')
    if r['author_id']!=u['id']:return json_error(403,'forbidden')
    if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'empty body')
    ch=core.get_channel(r['channel_id'])
    with core.tx() as c:
        mentions=resolve_mentions(c,d['body'],ch,u['id'])
        c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(d['body'],core.now_iso(),json.dumps(mentions),mid))
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        core.emit_event(c,r['channel_id'],'message.updated',{'message':msg_json(r)})
    return out({'message':msg_json(r)})

async def delete(req):
    u=require_user(req); mid=int(req.match_info['id'])
    c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    if r['deleted']:return out({'deleted':True})
    ch=core.get_channel(r['channel_id'])
    role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
    if r['author_id']!=u['id'] and role!='owner':return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
        core.emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid})
    return out({'deleted':True})

def reaction_list(mid):
    c=core.connect()
    rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall()
    c.close()
    out=[]
    for x in rows:
        z=next((a for a in out if a['emoji']==x['emoji']),None)
        if z:z['user_ids'].append(x['user_id']);z['count']+=1
        else:out.append({'emoji':x['emoji'],'count':1,'user_ids':[x['user_id']]})
    return out

async def react(req):
    u=require_user(req); mid=int(req.match_info['id'])
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not channel_access(u,ch):return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'archived')
    d=await read_json(req)
    if not d or not isinstance(d.get('emoji'),str) or not d['emoji']:return json_error(400,'invalid emoji')
    with core.tx() as c:
        old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],d['emoji'])).fetchone()
        if not old:
            c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],d['emoji']))
            core.emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':d['emoji'],'user_id':u['id']})
    return out({'reactions':reaction_list(mid)})

async def remove_reaction(req):
    u=require_user(req);mid=int(req.match_info['id']);emoji=req.match_info['emoji']
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not channel_access(u,ch):return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'archived')
    with core.tx() as c:
        old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
        if old:
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
            core.emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    return out({'reactions':reaction_list(mid)})

async def ws_handler(req):
    token=req.query.get('token',''); u=core.user_for_token(token)
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse(); await ws.prepare(req); gc=core.connect(); grow=gc.execute('SELECT COALESCE(MAX(id),0) n FROM global_events').fetchone(); global_since=grow['n']; gc.close(); conn_id=__import__('secrets').token_urlsafe(12); c0=core.connect(); c0.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],__import__('time').time())); c0.close(); subs={}
    async def poll():
        nonlocal global_since
        while not ws.closed:
            c0=core.connect(); c0.execute('UPDATE presence SET last_seen=? WHERE conn_id=?',(__import__('time').time(),conn_id)); c0.close()
            for e in core.globals_after(global_since):
                await ws.send_json(e); global_since += 1
            for ch,last in list(subs.items()):
                for e in core.events_after(ch,last): await ws.send_json(e); subs[ch]=e['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    try:
        async for m in ws:
            if m.type==WSMsgType.TEXT:
                d=json.loads(m.data)
                if d.get('type')=='subscribe':
                    ch=int(d['channel_id']); head=core.head_event_id(ch); subs[ch]=head; await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
                elif d.get('type')=='resume':
                    ch=int(d['channel_id']); since=int(d.get('since_event_id',0))
                    ev=core.events_after(ch,since)
                    for e in ev: await ws.send_json(e)
                    subs[ch]=ev[-1]['event_id'] if ev else since
                    await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':subs[ch]})
    finally:
        task.cancel(); c0=core.connect(); c0.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,)); c0.close()
    return ws
@web.middleware
async def errors(req,handler):
    try: return await handler(req)
    except web.HTTPUnauthorized: return json_error(401,'unauthorized')
    except web.HTTPForbidden: return json_error(403,'forbidden')
    except web.HTTPNotFound: return json_error(404,'not found')
    except web.HTTPBadRequest: return json_error(400,'bad request')
async def ws_members(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); a=c.execute('SELECT user_id,role FROM workspace_members WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return out({'members':[{'user_id':x['user_id'],'role':x['role']} for x in a]})
async def set_role(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    target=int(req.match_info['user_id']); c=core.connect(); m=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id']); d=await read_json(req)
    if not d or d.get('role') not in ('admin','member','guest'):return json_error(400,'invalid role')
    if target==w['owner_id'] or target==u['id']:return json_error(403,'forbidden')
    if role not in ('owner','admin') or (role=='admin' and m['role']=='admin'):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',(d['role'],w['id'],target))
    return out({'member':{'user_id':target,'role':d['role']}})
async def transfer(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if u['id']!=w['owner_id']:return json_error(403,'forbidden')
    d=await read_json(req); target=d.get('new_owner_id') if d else None
    c=core.connect(); m=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone(); c.close()
    if not m or m['role']!='admin':return json_error(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
        c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
    return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def patch_channel(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if ch['is_archived']:return json_error(423,'archived')
    c0=core.connect(); cm=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
    if not cm and core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    d=await read_json(req)
    if not d or not isinstance(d.get('topic'),str) or len(d['topic'])>250:return json_error(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],ch['id'])); ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone(); core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(ch)})
    return out({'channel':core.channel_to_json(ch)})
async def leave(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch) and not core.member_role(ch['workspace_id'],u['id']):return json_error(403,'forbidden')
    with core.tx() as c: core.remove_channel_member(c,ch['id'],u['id'])
    return out({'channel':core.channel_to_json(ch)})
async def archive(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    w=core.get_workspace(str(ch['workspace_id'])) if False else core.connect().execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if u['id']!=w['owner_id']:return json_error(403,'forbidden')
    val=1 if req.path.endswith('/archive') else 0
    with core.tx() as c:
        old=ch['is_archived']; c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id'])); ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        if old!=val: core.emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':core.channel_to_json(ch)})
    return out({'channel':core.channel_to_json(ch)})
def page_params(req,kind):
    if 'page' in req.query or 'offset' in req.query: raise ValueError()
    return core.parse_paging(req.query,kind)
async def messages(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    try:n,cur=page_params(req,'messages')
    except:return json_error(400,'invalid paging')
    c=core.connect(); q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'; args=[ch['id']]
    if cur is not None:q+=' AND id<?'; args.append(cur)
    q+=' ORDER BY id DESC LIMIT ?'; args.append(n+1); a=c.execute(q,args).fetchall(); c.close()
    more=len(a)>n; a=a[:n]; return out({'messages':[msg_json(x) for x in a],'next_cursor':core.encode_cursor('messages',a[-1]['id']) if more and a else None})
async def replies(req):
    u=require_user(req); c=core.connect(); parent=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    if not parent:return json_error(404,'not found')
    ch=core.get_channel(parent['channel_id'])
    if not channel_access(u,ch):return json_error(403,'forbidden')
    try:n,cur=page_params(req,'replies')
    except:return json_error(400,'invalid paging')
    c=core.connect(); q='SELECT * FROM messages WHERE channel_id=? AND parent_id=? AND deleted=0'; args=[ch['id'],parent['id']]
    if cur is not None:q+=' AND id>?'; args.append(cur)
    q+=' ORDER BY id ASC LIMIT ?';args.append(n+1); a=c.execute(q,args).fetchall();c.close(); more=len(a)>n;a=a[:n]
    return out({'replies':[msg_json(x) for x in a],'next_cursor':core.encode_cursor('replies',a[-1]['id']) if more and a else None})
async def pin(req):
    u=require_user(req); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not channel_access(u,ch):return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(m['id'],u['id'],core.now_iso())); p=c.execute('SELECT * FROM pins WHERE message_id=?',(m['id'],)).fetchone()
    return out({'pin':{'message_id':p['message_id'],'pinned_by':p['pinned_by'],'pinned_at':p['pinned_at']}})
async def unpin(req):
    u=require_user(req); c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(m['id'],))
    return out({'unpinned':True})
async def pins(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    try:n,cur=page_params(req,'pins')
    except:return json_error(400,'invalid paging')
    c=core.connect(); q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'; args=[ch['id']]
    if cur is not None: q+=' AND p.message_id<?'; args.append(cur)
    q+=' ORDER BY p.message_id DESC LIMIT ?'; args.append(n+1)
    a=c.execute(q,args).fetchall(); c.close(); more=len(a)>n; a=a[:n]
    return out({'pins':[{'message':msg_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in a],'next_cursor':core.encode_cursor('pins',a[-1]['message_id']) if more and a else None})
async def read_state_obj(uid,cid):
    c=core.connect()
    r=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone()
    last=r['last_read_event_id'] if r else 0
    rows=c.execute('SELECT m.*,e.event_id FROM messages m JOIN events e ON e.channel_id=m.channel_id AND json_extract(e.payload,\'$.message.id\')=m.id WHERE m.channel_id=? AND m.deleted=0 AND e.event_id>? AND m.author_id!=? ORDER BY e.event_id',(cid,last,uid)).fetchall()
    unread=len(rows); mention=sum(1 for x in rows if uid in (json.loads(x['mentions'] or '[]')))
    c.close()
    return {'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mention}
async def read_state(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    return out({'read_state':await read_state_obj(u['id'],ch['id'])})
async def set_read(req):
    u=require_user(req);ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not channel_access(u,ch):return json_error(403,'forbidden')
    d=await read_json(req)
    if not d or not isinstance(d.get('last_read_event_id'),int) or isinstance(d.get('last_read_event_id'),bool) or d['last_read_event_id']<0:return json_error(400,'invalid event')
    with core.tx() as c:c.execute('INSERT INTO read_state VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=max(last_read_event_id,excluded.last_read_event_id)',(u['id'],ch['id'],d['last_read_event_id']))
    return await read_state(req)

async def update_ws(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return json_error(403,'forbidden')
    d=await read_json(req)
    if not d:return json_error(400,'invalid body')
    if 'name' in d and (not isinstance(d['name'],str) or not d['name']):return json_error(400,'invalid name')
    if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join mode')
    with core.tx() as c:
        if 'name' in d:c.execute('UPDATE workspaces SET name=? WHERE id=?',(d['name'],w['id']))
        if 'join_mode' in d:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(d['join_mode'],w['id']))
    return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
def inv_json(r):
    return {k:r[k] for k in ('code','workspace_id','invited_user_id','max_uses','uses','expires_at','created_at')}
def inv_active(r):
    from datetime import datetime,timezone
    if r['revoked'] or r['uses']>=r['max_uses']:return False
    if r['expires_at']:
        try:return datetime.fromisoformat(r['expires_at'].replace('Z','+00:00'))>datetime.now(timezone.utc)
        except:return False
    return True
async def create_inv(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    d=await read_json(req)
    if d is None:return json_error(400,'invalid body')
    mi=d.get('max_uses',1); ex=d.get('expires_in_seconds')
    if not isinstance(mi,int) or mi<=0 or (ex is not None and (not isinstance(ex,int) or ex<=0)):return json_error(400,'invalid invitation')
    target=d.get('invited_user_id')
    if 'invited_username' in d:
        if not isinstance(d['invited_username'],str):return json_error(400,'invalid target')
        c=core.connect(); z=c.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone();c.close()
        if not z:return json_error(404,'user not found')
        target=z['id']
    if target is not None:
        if not isinstance(target,int):return json_error(400,'invalid target')
        c=core.connect();z=c.execute('SELECT id FROM users WHERE id=?',(target,)).fetchone();c.close()
        if not z:return json_error(404,'user not found')
        mi=1
    from datetime import datetime,timezone,timedelta
    exp=(datetime.now(timezone.utc)+timedelta(seconds=ex)).isoformat(timespec='milliseconds').replace('+00:00','Z') if ex is not None else None
    code=__import__('secrets').token_urlsafe(18)
    with core.tx() as c:
        c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,0)',(code,w['id'],target,mi,0,exp,core.now_iso()))
        r=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
    return out({'invitation':inv_json(r)},201)
async def list_inv(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    c=core.connect();a=c.execute('SELECT * FROM invitations WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
    return out({'invitations':[inv_json(x) for x in a if inv_active(x)]})
async def accept_inv(req):
    u=require_user(req);c=core.connect();r=c.execute('SELECT * FROM invitations WHERE code=?',(req.match_info['code'],)).fetchone();c.close()
    if not r or not inv_active(r):return json_error(404,'not found')
    if r['invited_user_id'] is not None and r['invited_user_id']!=u['id']:return json_error(403,'targeted invitation')
    w=core.get_workspace(str(r['workspace_id'])) if False else None
    c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone();mem=c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(r['workspace_id'],u['id'])).fetchone();c.close()
    if not mem:
        with core.tx() as c:
            c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(r['code'],));core.add_workspace_member(c,r['workspace_id'],u['id'],'member')
    return out({'workspace':core.workspace_to_json(w)})
async def revoke_inv(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    c=core.connect();r=c.execute('SELECT * FROM invitations WHERE code=? AND workspace_id=?',(req.match_info['code'],w['id'])).fetchone();c.close()
    if not r:return json_error(404,'not found')
    with core.tx() as c:c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(r['code'],))
    return out({'deleted':True})
async def search(req):
    u=require_user(req)
    q=req.query.get('q')
    if not isinstance(q,str) or not q.strip():
        return json_error(400,'missing query')
    try:
        n,cur=page_params(req,'search')
    except:
        return json_error(400,'invalid paging')
    c=core.connect()
    sql="SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=? LEFT JOIN workspace_members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=? WHERE m.deleted=0 AND m.parent_id IS NULL AND (cm.user_id IS NOT NULL OR (ch.is_private=0 AND wm.user_id IS NOT NULL)) AND lower(m.body) LIKE lower(?)"
    args=[u['id'],u['id'],'%'+q+'%']
    if req.query.get('workspace_id'):
        try: wid=int(req.query['workspace_id'])
        except: return json_error(400,'invalid workspace_id')
        sql+=' AND ch.workspace_id=?'
        args.append(wid)
    if cur is not None:
        sql+=' AND m.id<?'
        args.append(cur)
    sql+=' ORDER BY m.id DESC LIMIT ?'
    args.append(n+1)
    a=c.execute(sql,args).fetchall()
    c.close()
    more=len(a)>n
    a=a[:n]
    return out({'results':[msg_json(x) for x in a],'next_cursor':core.encode_cursor('search',a[-1]['id']) if more and a else None})


async def create_dm(req):
    u=require_user(req); d=await read_json(req)
    if not d or not isinstance(d.get('user_id'),int) or isinstance(d.get('user_id'),bool):return json_error(400,'invalid user_id')
    other=d['user_id']; c=core.connect(); z=c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone(); c.close()
    if not z:return json_error(404,'user not found')
    if other==u['id']:return json_error(400,'invalid user_id')
    a,b=sorted((u['id'],other))
    with core.tx() as c:
        old=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if old:return out({'channel_id':old['channel_id']})
        c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',('',1))
        cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid))
        c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,b))
    return out({'channel_id':cid})
async def upload_file(req):
    u=require_user(req)
    try:
        form=await req.multipart(); part=await form.next()
        if not part or part.name!='file':return json_error(400,'file required')
        data=await part.read()
    except: return json_error(400,'invalid multipart')
    if len(data)>10485760:return json_error(413,'file too large')
    name=part.filename or 'file'; ctype=part.headers.get('Content-Type','application/octet-stream')
    with core.tx() as c:
        c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],name,ctype,len(data),core.now_iso()))
        fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    os.makedirs('/app/data/files',exist_ok=True)
    with open('/app/data/files/'+str(fid),'wb') as f:f.write(data)
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    return out({'file':{'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}},201)
def file_obj(r):
    return {'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}
async def get_file(req):
    require_user(req);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    return out({'file':file_obj(r)})
async def download_file(req):
    require_user(req);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    try:data=open('/app/data/files/'+str(r['id']),'rb').read()
    except:return json_error(404,'not found')
    return web.Response(body=data,content_type=r['content_type'],headers={'Content-Disposition':'attachment; filename="'+r['filename'].replace('"','')+'"'})

def group_obj(r):
    c=core.connect(); a=c.execute('SELECT user_id FROM group_members WHERE group_id=?',(r['id'],)).fetchall();c.close()
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':[x['user_id'] for x in a]}
async def groups(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect();a=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
    return out({'groups':[group_obj(x) for x in a]})
async def create_group(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    d=await read_json(req)
    if not d or not isinstance(d.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['handle']) or not isinstance(d.get('name'),str) or not isinstance(d.get('user_ids'),list):return json_error(400,'invalid group')
    c=core.connect(); bad=[x for x in d['user_ids'] if not isinstance(x,int) or not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone()];dup=c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(w['id'],d['handle'])).fetchone();c.close()
    if bad:return json_error(400,'invalid members')
    if dup:return json_error(409,'duplicate handle')
    with core.tx() as c:
        c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id,created_at) VALUES(?,?,?,?,?)',(w['id'],d['handle'],d['name'],u['id'],core.now_iso()));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('DELETE FROM group_members WHERE group_id=?',(gid,))
        for x in d['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,x))
        r=c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone()
    return out({'group':group_obj(r)},201)
async def patch_group(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone();c.close()
    if not r:return json_error(404,'not found')
    if r['creator_id']!=u['id'] and u['id']!=w['owner_id']:return json_error(403,'forbidden')
    d=await read_json(req)
    if not d:return json_error(400,'invalid body')
    with core.tx() as c:
        if 'name' in d:
            if not isinstance(d['name'],str) or not d['name']:return json_error(400,'invalid name')
            c.execute('UPDATE groups SET name=? WHERE id=?',(d['name'],r['id']))
        if 'user_ids' in d:
            if not isinstance(d['user_ids'],list):return json_error(400,'invalid members')
            for x in d['user_ids']:
                if not isinstance(x,int) or not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone():return json_error(400,'invalid members')
            c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],))
            for x in d['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(r['id'],x))
        r=c.execute('SELECT * FROM groups WHERE id=?',(r['id'],)).fetchone()
    return out({'group':group_obj(r)})
async def delete_group(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone();c.close()
    if not r:return out({'deleted':True})
    if r['creator_id']!=u['id'] and u['id']!=w['owner_id']:return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],))
        c.execute('DELETE FROM groups WHERE id=?',(r['id'],))
    return out({'deleted':True})
def app():
    a=web.Application(middlewares=[errors],client_max_size=11*1024*1024)
    a.router.add_get('/',index);a.router.add_get('/api/health',health);a.router.add_get('/api/users/{id}',get_user);a.router.add_patch('/api/users/me',update_profile);a.router.add_post('/api/dms',create_dm);a.router.add_post('/api/files',upload_file);a.router.add_get('/api/files/{id}',get_file);a.router.add_get('/api/files/{id}/download',download_file);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces);a.router.add_get('/api/workspaces/{slug}/groups',groups);a.router.add_post('/api/workspaces/{slug}/groups',create_group);a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',patch_group);a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',delete_group);a.router.add_get('/api/search',search);a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_patch('/api/workspaces/{slug}',update_ws);a.router.add_get('/api/workspaces/{slug}/invitations',list_inv);a.router.add_post('/api/workspaces/{slug}/invitations',create_inv);a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_inv);a.router.add_post('/api/invitations/{code}/accept',accept_inv);a.router.add_get('/api/workspaces/{slug}/members',ws_members);a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',set_role);a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer);a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/channels/{id}/join',join);a.router.add_post('/api/channels/{id}/leave',leave);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_post('/api/channels/{id}/archive',archive);a.router.add_post('/api/channels/{id}/unarchive',archive);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',post_message);a.router.add_get('/api/channels/{id}/pins',pins);a.router.add_get('/api/channels/{id}/read',read_state);a.router.add_post('/api/channels/{id}/read',set_read)
    a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_post('/api/messages/{id}/pin',pin);a.router.add_delete('/api/messages/{id}/pin',unpin);a.router.add_patch('/api/messages/{id}',edit);a.router.add_delete('/api/messages/{id}',delete);a.router.add_post('/api/messages/{id}/reactions',react);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',remove_reaction);a.router.add_get('/api/ws',ws_handler)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(app(),host='127.0.0.1',port=8000+NODE)
