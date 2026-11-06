import asyncio, json, re, os, sys, argparse, hashlib, mimetypes, secrets
from zoneinfo import ZoneInfo
from aiohttp import web, WSMsgType
import core
NODE=0
def js(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return js({'error':msg},status)
async def body(req):
    try: return await req.json()
    except: return None
def auth(req):
    h=req.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
async def need(req):
    u=auth(req)
    if not u: raise web.HTTPUnauthorized(text=json.dumps({'error':'unauthorized'}),content_type='application/json')
    return u
def role(wid,uid): return core.member_role(wid,uid)
def mention_ids(c,r):
    body=r['body'] or ''
    names=re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body)
    ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    ids=[]
    if ch and ch['workspace_id'] and names:
        marks=','.join('?'*len(names))
        sql='SELECT u.id FROM users u JOIN members wm ON wm.user_id=u.id JOIN channel_members cm ON cm.user_id=u.id WHERE wm.workspace_id=? AND cm.channel_id=? AND u.username IN ('+marks+')'
        ids.extend(x['id'] for x in c.execute(sql,(ch['workspace_id'],ch['id'],*names)).fetchall())
    if ch and '@channel' in body:
        ids.extend(x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id<>?',(ch['id'],r['author_id'])).fetchall())
    if ch and ch['workspace_id']:
        handles=[x for x in names if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(ch['workspace_id'],x)).fetchone()]
        for h in handles:
            g=c.execute('SELECT user_ids FROM groups_ WHERE workspace_id=? AND handle=?',(ch['workspace_id'],h)).fetchone()
            if g:
                ids.extend(z['user_id'] for z in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id<>?',(ch['id'],r['author_id'])).fetchall() if z['user_id'] in json.loads(g['user_ids']))
        if '@here' in body:
            ids.extend(z['user_id'] for z in c.execute('SELECT cm.user_id FROM channel_members cm JOIN presence p ON p.user_id=cm.user_id WHERE cm.channel_id=? AND cm.user_id<>? AND p.last_seen>?',(ch['id'],r['author_id'],__import__('time').time()-3)).fetchall())
    return list(dict.fromkeys(x for x in ids if x!=r['author_id']))
def reactions_for(c,mid):
    rows=c.execute('SELECT emoji,MIN(rowid) first_added FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY first_added',(mid,)).fetchall()
    return [{'emoji':x['emoji'],'count':c.execute('SELECT count(*) n FROM reactions WHERE message_id=? AND emoji=?',(mid,x['emoji'])).fetchone()['n'],'user_ids':[z['user_id'] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=? ORDER BY rowid',(mid,x['emoji']))]} for x in rows]
def msgjson(c,r):
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    replies=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':core.user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[{'id':f['id'],'uploader_id':f['uploader_id'],'filename':f['filename'],'content_type':f['content_type'],'size':f['size'],'created_at':f['created_at']} for f in c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],))],'reactions':reactions_for(c,r['id']),'mentions':mention_ids(c,r)}
def canread(u,ch):
    c=core.connect()
    x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
    if x:
        c.close(); return True
    if ch['workspace_id'] and not ch['is_private']:
        x=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone()
    c.close()
    return bool(x)
async def health(req): return js({'status':'ok','node_id':NODE})
async def register(req):
    d=await body(req)
    if not isinstance(d,dict) or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d['username']) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(d['username'],)).fetchone(): return err(409,'username exists')
        c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username'],core.now_iso()))
        u=c.execute('SELECT * FROM users WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone(); t=core.issue_token(c,u['id'])
    return js({'user':core.user_to_json(u),'token':t},201)
async def login(req):
    d=await body(req)
    if not isinstance(d,dict): return err(400,'invalid body')
    c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username'),)).fetchone(); c.close()
    if not u or not isinstance(d.get('password'),str) or not core.verify_password(d['password'],u['password']): return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return js({'user':core.user_to_json(u),'token':t})
async def me(req):
    u=await need(req); return js({'user':core.user_to_json(u)})
async def workspaces(req):
    u=await need(req); c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return js({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_ws(req):
    u=await need(req); d=await body(req)
    if not isinstance(d,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))) or not isinstance(d.get('name'),str) or not d['name']: return err(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(d['slug'],)).fetchone(): return err(409,'slug exists')
        c.execute('INSERT INTO workspaces(slug,name,owner_id,created_at) VALUES(?,?,?,?)',(d['slug'],d['name'],u['id'],core.now_iso())); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); cid=core.create_channel_row(c,wid,'general')
        core.add_channel_member(c,cid,u['id']); w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    return js({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
async def workspace(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    if not role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect()
    include=req.query.get('include_archived') in ('1','true')
    rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],include)).fetchall()
    rows=[x for x in rows if not x['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone()]
    states=[]
    for x in rows:
        rr=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone()
        last=rr['last_read_event_id'] if rr else 0
        ev=c.execute("SELECT e.event_id,m.* FROM events e LEFT JOIN messages m ON m.id=json_extract(e.payload, '$.message.id') WHERE e.channel_id=? AND e.event_id>? ORDER BY e.event_id",(x['id'],last)).fetchall()
        vis=[z for z in ev if z['author_id'] is not None and z['author_id']!=u['id']]
        states.append({'channel_id':x['id'],'last_read_event_id':last,'unread_count':len(vis),'mention_count':sum(1 for z in vis if u['id'] in mention_ids(c,z))})
    c.close()
    return js({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':states})
async def members(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    if not role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return js({'members':[dict(x) for x in rows]})
async def create_channel(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug']); d=await body(req)
    if not w:return err(404,'workspace not found')
    rr=role(w['id'],u['id'])
    if rr not in ('owner','admin','member'):return err(403,'forbidden')
    if not isinstance(d,dict) or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']) or ('topic' in d and (not isinstance(d['topic'],str) or len(d['topic'])>250)):return err(400,'invalid channel')
    if d.get('is_private') and rr not in ('owner','admin'):return err(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return err(409,'channel exists')
        cid=core.create_channel_row(c,w['id'],d['name'],d.get('is_private',False),d.get('topic','')); core.add_channel_member(c,cid,u['id']); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    return js({'channel':core.channel_to_json(ch)},201)
async def channel(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'channel not found')
    if not canread(u,ch):return err(403,'forbidden')
    return js({'channel':core.channel_to_json(ch)})
async def join(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'channel not found')
    if ch['is_private'] and not canread(u,ch):return err(403,'private channel')
    if ch['workspace_id'] and role(ch['workspace_id'],u['id'])=='guest':return err(403,'guest')
    if ch['workspace_id'] and not role(ch['workspace_id'],u['id']):
        w=core.get_workspace((core.connect().execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()['slug']))
        if w['join_mode']!='open':return err(403,'invite required')
        with core.tx() as c:core.add_workspace_member(c,ch['workspace_id'],u['id'])
    with core.tx() as c:
        existed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        if not existed:
            core.add_channel_member(c,ch['id'],u['id']); core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
    return js({'channel':core.channel_to_json(ch)})
async def messages(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'channel not found')
    if not canread(u,ch):return err(403,'forbidden')
    if 'offset' in req.query or 'page' in req.query:return err(400,'cursor pagination required')
    try: lim,cur=core.parse_paging(req.query,'messages')
    except:return err(400,'invalid cursor')
    c=core.connect(); q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'; args=[ch['id']]
    if cur:q+=' AND id<?';args.append(cur)
    q+=' ORDER BY id DESC LIMIT ?';args.append(lim+1); rows=c.execute(q,args).fetchall(); out=[msgjson(c,x) for x in rows[:lim]]; nxt=core.encode_cursor('messages',rows[lim-1]['id']) if len(rows)>lim else None; c.close()
    return js({'messages':out,'next_cursor':nxt})
async def slash(user,ch,body):
    if body.startswith('//'): return None, body[1:]
    parts=body.split(None,1); cmd=parts[0].lower()
    arg=parts[1] if len(parts)>1 else ''
    if cmd=='/me':
        if not arg.strip(): return err(400,'/me requires text'),None
        return None,body
    if cmd=='/shrug':
        return None,(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
    if cmd=='/topic':
        if not arg.strip(): return err(400,'topic required'),None
        if not canread(user,ch) and role(ch['workspace_id'],user['id'])!='owner': return err(403,'forbidden'),None
        if ch['is_archived']: return err(423,'archived'),None
        with core.tx() as c:
            c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']))
            x=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
            core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(x)})
        return js({'message':None,'channel':core.channel_to_json(x)},201),None
    if cmd=='/invite':
        m=re.fullmatch(r'@([A-Za-z0-9_]+)',arg.strip())
        if not m:return err(400,'invalid invite'),None
        if not ch['workspace_id'] or not role(ch['workspace_id'],user['id']):return err(403,'forbidden'),None
        c=core.connect(); target=c.execute('SELECT * FROM users WHERE username=?',(m.group(1),)).fetchone(); c.close()
        if not target:return err(404,'user not found'),None
        with core.tx() as c:
            core.add_channel_member(c,ch['id'],target['id'])
            core.emit_event(c,ch['id'],'member.joined',{'user_id':target['id']})
        return js({'message':None,'invited_user':core.user_to_json(target)},201),None
    if cmd in ('/archive','/unarchive'):
        if role(ch['workspace_id'],user['id'])!='owner':return err(403,'forbidden'),None
        with core.tx() as c:
            val=1 if cmd=='/archive' else 0
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
            x=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
            core.emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':core.channel_to_json(x)})
        return js({'message':None,'channel':core.channel_to_json(x)},201),None
    return err(400,'unknown command'),None

async def post(req):
    u=await need(req); cid=int(req.match_info['id']); ch=core.get_channel(cid); d=await body(req)
    if not ch:return err(404,'channel not found')
    if not canread(u,ch):return err(403,'forbidden')
    if isinstance(d,dict) and isinstance(d.get('body'),str) and d['body'].startswith('/') and not d['body'].startswith('//'):
        response, transformed=await slash(u,ch,d['body'])
        if response is not None:return response
        d['body']=transformed
    elif isinstance(d,dict) and isinstance(d.get('body'),str) and d['body'].startswith('//'):
        d['body']=d['body'][1:]
    if ch['is_archived']:return err(423,'archived')
    if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'empty message')
    file_ids=d.get('file_ids',[])
    if not isinstance(file_ids,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in file_ids):return err(400,'invalid file_ids')
    if 'parent_id' in d and d['parent_id'] is not None and (not isinstance(d['parent_id'],int) or isinstance(d['parent_id'],bool)):return err(400,'invalid parent_id')
    with core.tx() as c:
        if file_ids:
            marks=','.join('?'*len(file_ids))
            fs=c.execute('SELECT * FROM files WHERE id IN ('+marks+')',(file_ids)).fetchall()
            if len(fs)!=len(set(file_ids)) or any(f['uploader_id']!=u['id'] or f['message_id'] is not None for f in fs):return err(400,'invalid files')
        c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],d['body'],d.get('parent_id'),core.now_iso())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        if file_ids:c.execute('UPDATE files SET message_id=? WHERE id IN ('+','.join('?'*len(file_ids))+')',(mid,*file_ids))
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); frame=core.emit_event(c,cid,'message.reply' if d.get('parent_id') else 'message.created',{'message':msgjson(c,r)})
    return js({'message':msgjson(core.connect(),r)},201)
async def edit(req):
    u=await need(req); d=await body(req); mid=int(req.match_info['id'])
    c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not r:return err(404,'message not found')
    if r['deleted']:return err(409,'message deleted')
    if r['author_id']!=u['id']:return err(403,'forbidden')
    if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'invalid body')
    ch=core.get_channel(r['channel_id'])
    if ch['is_archived']:return err(423,'archived')
    with core.tx() as c:
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],core.now_iso(),mid))
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        core.emit_event(c,r['channel_id'],'message.updated',{'message':msgjson(c,r)})
    return js({'message':msgjson(core.connect(),r)})
async def delete(req):
    u=await need(req); mid=int(req.match_info['id'])
    c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not r:return err(404,'message not found')
    if r['deleted']:return js({'deleted':True})
    ch=core.get_channel(r['channel_id'])
    owner=ch['workspace_id'] and role(ch['workspace_id'],u['id'])=='owner'
    if r['author_id']!=u['id'] and not owner:return err(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
        core.emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid})
        c.execute('DELETE FROM reactions WHERE message_id=?',(mid,))
    return js({'deleted':True})
async def react(req):
    u=await need(req); mid=int(req.match_info['id']); d=await body(req)
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone(); c.close()
    if not m:return err(404,'message not found')
    ch=core.get_channel(m['channel_id'])
    if not canread(u,ch):return err(403,'forbidden')
    if ch['is_archived']:return err(423,'archived')
    if not isinstance(d,dict) or not isinstance(d.get('emoji'),str) or not d['emoji']:return err(400,'invalid emoji')
    with core.tx() as c:
        exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],d['emoji'])).fetchone()
        if not exists:
            c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],d['emoji']))
            core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':mid,'emoji':d['emoji'],'user_id':u['id']})
        out=reactions_for(c,mid)
    return js({'reactions':out})
async def unreact(req):
    u=await need(req); mid=int(req.match_info['id']); emoji=req.match_info['emoji']
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone(); c.close()
    if not m:return err(404,'message not found')
    ch=core.get_channel(m['channel_id'])
    if not canread(u,ch):return err(403,'forbidden')
    if ch['is_archived']:return err(423,'archived')
    with core.tx() as c:
        exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
        if exists:
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
            core.emit_event(c,m['channel_id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
        out=reactions_for(c,mid)
    return js({'reactions':out})
async def replies(req):
    u=await need(req); pid=int(req.match_info['id']); c=core.connect(); p=c.execute('SELECT * FROM messages WHERE id=?',(pid,)).fetchone()
    if not p:return err(404,'message not found')
    try: lim,cur=core.parse_paging(req.query,'replies')
    except:return err(400,'invalid cursor')
    q='SELECT * FROM messages WHERE parent_id=? AND deleted=0'; args=[pid]
    if cur:q+=' AND id>?';args.append(cur)
    q+=' ORDER BY id LIMIT ?';args.append(lim+1); rows=c.execute(q,args).fetchall()
    out=[msgjson(c,x) for x in rows[:lim]]; nxt=core.encode_cursor('replies',rows[lim-1]['id']) if len(rows)>lim else None; c.close()
    return js({'replies':out,'next_cursor':nxt})
async def patch_profile(req):
    u=await need(req); d=await body(req)
    if not isinstance(d,dict):return err(400,'invalid body')
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if any(k not in allowed for k in d):return err(400,'invalid field')
    limits={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512)}
    for k,v in d.items():
        if not isinstance(v,str):return err(400,'invalid field')
        if k in limits and not (limits[k][0]<=len(v)<=limits[k][1]):return err(400,'invalid field')
        if k=='timezone':
            try: ZoneInfo(v)
            except: return err(400,'invalid timezone')
    with core.tx() as c:
        for k,v in d.items(): c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
        r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        core.emit_global(c,{'type':'user.updated','user':core.user_to_json(r)})
    return js({'user':core.user_to_json(r)})
async def ws(req):
    token=req.query.get('token',''); u=core.user_for_token(token)
    if not u:return err(401,'unauthorized')
    w=web.WebSocketResponse(); await w.prepare(req); subscribed={}
    gc=core.connect(); gr=gc.execute('SELECT COALESCE(MAX(id),0) n FROM global_events').fetchone(); global_seen=gr['n']; gc.close()
    conn_id=secrets.token_urlsafe(12)
    with core.tx() as c:c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],__import__('time').time()))
    async def poll():
        nonlocal global_seen
        try:
            while True:
                for gid,e in core.globals_after(global_seen):
                    global_seen=gid; await w.send_json(e)
                for cid,last in list(subscribed.items()):
                    for e in core.events_after(cid,last):
                        subscribed[cid]=e['event_id']; await w.send_json(e)
                await asyncio.sleep(.05)
        except: pass
    task=asyncio.create_task(poll())
    try:
        async for m in w:
            if m.type==WSMsgType.TEXT:
                try:d=json.loads(m.data)
                except:continue
                cid=d.get('channel_id')
                if d.get('type')=='subscribe' and isinstance(cid,int):
                    subscribed[cid]=core.head_event_id(cid)
                    await w.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subscribed[cid]})
                elif d.get('type')=='resume' and isinstance(cid,int):
                    since=int(d.get('since_event_id',0)); subscribed[cid]=since
                    for e in core.events_after(cid,since): subscribed[cid]=e['event_id']; await w.send_json(e)
                    await w.send_json({'type':'resumed','channel_id':cid,'head_event_id':core.head_event_id(cid)})
            elif m.type in (WSMsgType.CLOSE,WSMsgType.ERROR):break
    finally:
        task.cancel()
        with core.tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
    return w

async def patch_member(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    uid=int(req.match_info['user_id']); d=await body(req); c=core.connect()
    target=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone(); c.close()
    if not target:return err(404,'member not found')
    rr=role(w['id'],u['id']); old=target['role']; new=d.get('role') if isinstance(d,dict) else None
    if new not in ('owner','admin','member','guest'):return err(400,'invalid role')
    if new=='owner':return err(400,'use transfer')
    if uid==w['owner_id'] or uid==u['id'] and old=='owner':return err(403,'forbidden')
    if rr not in ('owner','admin') or (rr=='admin' and old=='admin'):return err(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(new,w['id'],uid))
        r=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone()
    return js({'member':dict(r)})
async def transfer(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    if u['id']!=w['owner_id']:return err(403,'forbidden')
    d=await body(req); uid=d.get('new_owner_id') if isinstance(d,dict) else None
    c=core.connect(); t=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone(); c.close()
    if not t or t['role']!='admin':return err(400,'target must be admin')
    with core.tx() as c:
        c.execute("UPDATE members SET role='admin' WHERE workspace_id=? AND user_id=?",(w['id'],u['id']))
        c.execute("UPDATE members SET role='owner' WHERE workspace_id=? AND user_id=?",(w['id'],uid))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(uid,w['id']))
        x=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return js({'workspace':core.workspace_to_json(x)})
async def channel_members(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'channel not found')
    if not canread(u,ch):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return js({'members':[core.user_to_json(x) for x in rows]})
async def leave(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'channel not found')
    with core.tx() as c:
        existed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        if existed:
            c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
            core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return js({'channel':core.channel_to_json(ch)})
async def patch_channel(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'channel not found')
    if not (role(ch['workspace_id'],u['id'])=='owner' or core.connect().execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()):return err(403,'forbidden')
    if ch['is_archived']:return err(423,'archived')
    d=await body(req)
    if not isinstance(d,dict) or not isinstance(d.get('topic'),str) or len(d['topic'])>250:return err(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],ch['id'])); ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone(); core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(ch)})
    return js({'channel':core.channel_to_json(ch)})
async def archive(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'channel not found')
    if role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
    action='unarchive' if req.path.endswith('/unarchive') else 'archive'
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if action=='archive' else 0,ch['id']))
        ch2=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.archived' if action=='archive' else 'channel.unarchived',{'channel':core.channel_to_json(ch2)})
    return js({'channel':core.channel_to_json(ch2)})
async def pin(req):
    u=await need(req); mid=int(req.match_info['id']); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m:return err(404,'message not found')
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()))
        x=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    return js({'pin':dict(x)})
async def unpin(req):
    u=await need(req); mid=int(req.match_info['id'])
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
    return js({'unpinned':True})
async def pins(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'channel not found')
    if not canread(u,ch):return err(403,'forbidden')
    try: lim,cur=core.parse_paging(req.query,'pins')
    except:return err(400,'invalid cursor')
    c=core.connect(); q='SELECT p.*,m.*,pinned_by FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'; args=[ch['id']]
    if cur:q+=' AND p.message_id<?';args.append(cur)
    q+=' ORDER BY p.pinned_at DESC LIMIT ?';args.append(lim+1); rows=c.execute(q,args).fetchall()
    out=[]
    for x in rows[:lim]:
        out.append({'message':msgjson(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']})
    nxt=core.encode_cursor('pins',rows[lim-1]['message_id']) if len(rows)>lim else None
    c.close(); return js({'pins':out,'next_cursor':nxt})


async def patch_workspace(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    if role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    d=await body(req)
    if not isinstance(d,dict):return err(400,'invalid body')
    if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return err(400,'invalid join_mode')
    if 'name' in d and not isinstance(d['name'],str):return err(400,'invalid name')
    with core.tx() as c:
        if 'name' in d:c.execute('UPDATE workspaces SET name=? WHERE id=?',(d['name'],w['id']))
        if 'join_mode' in d:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(d['join_mode'],w['id']))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return js({'workspace':core.workspace_to_json(w)})
async def create_inv(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    if role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    d=await body(req)
    if not isinstance(d,dict):return err(400,'invalid body')
    for k in ('expires_in_seconds','max_uses'):
        if k in d and (not isinstance(d[k],int) or isinstance(d[k],bool) or d[k]<=0):return err(400,'invalid invitation')
    target=None
    c=core.connect()
    if d.get('invited_user_id') is not None:
        target=c.execute('SELECT id FROM users WHERE id=?',(d['invited_user_id'],)).fetchone()
        if not target:c.close();return err(400,'unknown user')
    if d.get('invited_username') is not None:
        target=c.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone()
        if not target:c.close();return err(400,'unknown user')
    c.close()
    code=core.secrets.token_urlsafe(12)
    with core.tx() as c:
        c.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,expires,max_uses) VALUES(?,?,?,?,?)',(code,w['id'],target['id'] if target else None,core.time.time()+d.get('expires_in_seconds',86400),(1 if target else d.get('max_uses',1))))
        r=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
    return js({'invitation':dict(r)},201)
async def list_inv(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    if role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND expires>?',(w['id'],core.time.time())).fetchall(); c.close()
    return js({'invitations':[dict(x) for x in rows]})
async def accept_inv(req):
    u=await need(req); code=req.match_info['code']; c=core.connect(); x=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone(); c.close()
    if not x or x['revoked'] or x['uses']>=x['max_uses'] or x['expires']<core.time.time():return err(404,'invitation not found')
    if x['invited_user_id'] and x['invited_user_id']!=u['id']:return err(403,'targeted invitation')
    with core.tx() as c:
        core.add_workspace_member(c,x['workspace_id'],u['id']); c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,))
    return js({'workspace':core.workspace_to_json(core.get_workspace_by_id(x['workspace_id'])) if hasattr(core,'get_workspace_by_id') else {}})
async def del_inv(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    if role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    with core.tx() as c:
        x=c.execute('SELECT * FROM invitations WHERE code=? AND workspace_id=?',(req.match_info['code'],w['id'])).fetchone()
        if not x:return err(404,'invitation not found')
        c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(x['code'],))
    return js({'deleted':True})


async def search(req):
    u=await need(req); q=req.query.get('q')
    if not q:return err(400,'q required')
    wid=req.query.get('workspace_id')
    if wid is not None:
        try: wid=int(wid)
        except: return err(400,'invalid workspace_id')
    try: lim,cur=core.parse_paging(req.query,'search')
    except:return err(400,'invalid paging')
    c=core.connect()
    sql="SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=? WHERE m.deleted=0 AND lower(m.body) LIKE lower(?) AND (ch.is_private=0 OR EXISTS (SELECT 1 FROM channel_members cm WHERE cm.channel_id=ch.id AND cm.user_id=?))"
    args=[u['id'],f'%{q}%',u['id']]
    if wid is not None: sql+=' AND ch.workspace_id=?'; args.append(wid)
    sql+=' ORDER BY m.id DESC'
    rows=c.execute(sql,args).fetchall()
    if cur: rows=[x for x in rows if x['id']<cur]
    out=[msgjson(c,x) for x in rows[:lim]]; nxt=core.encode_cursor('search',rows[lim-1]['id']) if len(rows)>lim else None; c.close()
    return js({'results':out,'next_cursor':nxt})
async def readstate(req):
    u=await need(req); cid=int(req.match_info['id']); ch=core.get_channel(cid)
    if not ch:return err(404,'channel not found')
    if not canread(u,ch):return err(403,'forbidden')
    c=core.connect(); rr=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
    last=rr['last_read_event_id'] if rr else 0
    rows=c.execute("SELECT e.event_id,m.* FROM events e LEFT JOIN messages m ON m.id=json_extract(e.payload, '$.message.id') WHERE e.channel_id=? AND e.event_id>? ORDER BY e.event_id",(cid,last)).fetchall()
    visible=[x for x in rows if x['author_id'] is not None and x['author_id']!=u['id']]
    unread=len(visible); mentions=sum(1 for x in visible if u['id'] in mention_ids(c,x))
    c.close()
    return js({'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mentions}})
async def markread(req):
    u=await need(req); cid=int(req.match_info['id']); d=await body(req)
    if not isinstance(d,dict) or not isinstance(d.get('last_read_event_id'),int) or isinstance(d.get('last_read_event_id'),bool):return err(400,'invalid event id')
    ch=core.get_channel(cid)
    if not ch:return err(404,'channel not found')
    if not canread(u,ch):return err(403,'forbidden')
    with core.tx() as c:
        old=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
        v=max(old['last_read_event_id'] if old else 0,d['last_read_event_id'])
        c.execute('INSERT INTO read_state(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],cid,v))
    return await readstate(req)


async def missing_user(req):
    u=await need(req)
    c=core.connect(); x=c.execute('SELECT * FROM users WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    if not x:return err(404,'user not found')
    return js({'user':core.user_to_json(x)})
async def missing_dm(req):
    u=await need(req); d=await body(req)
    if not isinstance(d,dict) or not isinstance(d.get('user_id'),int) or isinstance(d.get('user_id'),bool):return err(400,'invalid user_id')
    other=d['user_id']
    if other==u['id']:return err(400,'invalid user_id')
    c=core.connect(); x=c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone(); pair=tuple(sorted((u['id'],other)))
    old=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',pair).fetchone(); c.close()
    if not x:return err(404,'user not found')
    if old:return js({'channel_id':old['channel_id']})
    with core.tx() as c:
        c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)')
        cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO dms VALUES(?,?,?)',(*pair,cid))
        c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,u['id'],cid,other))
    return js({'channel_id':cid})
async def missing_file(req):
    u=await need(req)
    if req.method=='POST':
        try:
            rd=await req.multipart(); part=await rd.next()
            if not part or part.name!='file':return err(400,'file required')
            data=await part.read(decode=False)
        except: return err(400,'invalid multipart')
        if len(data)>10485760:return err(413,'file too large')
        fn=part.filename or 'file'; ct=part.headers.get('Content-Type','application/octet-stream')
        with core.tx() as c:
            c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],fn,ct,len(data),core.now_iso()))
            fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        os.makedirs('/app/data/files',exist_ok=True)
        open('/app/data/files/'+str(fid),'wb').write(data)
        return js({'file':{'id':fid,'uploader_id':u['id'],'filename':fn,'content_type':ct,'size':len(data),'created_at':core.connect().execute('SELECT created_at FROM files WHERE id=?',(fid,)).fetchone()['created_at']}},201)
    try: fid=int(req.match_info['id'])
    except: return err(404,'file not found')
    c=core.connect(); f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone(); c.close()
    if not f:return err(404,'file not found')
    if req.path.endswith('/download'):
        return web.Response(body=open('/app/data/files/'+str(fid),'rb').read(),content_type=f['content_type'],headers={'Content-Disposition':f'attachment; filename="{f["filename"]}"'})
    return js({'file':{'id':f['id'],'uploader_id':f['uploader_id'],'filename':f['filename'],'content_type':f['content_type'],'size':f['size'],'created_at':f['created_at']}})
def group_json(r):
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'] or '[]')}
async def missing_groups(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    rr=role(w['id'],u['id'])
    if not rr:return err(403,'forbidden')
    if req.method=='GET':
        c=core.connect(); rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
        return js({'groups':[group_json(x) for x in rows]})
    d=await body(req)
    if not isinstance(d,dict) or not isinstance(d.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['handle']) or not isinstance(d.get('name'),str) or not isinstance(d.get('user_ids'),list):return err(400,'invalid group')
    c=core.connect()
    if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],d['handle'])).fetchone():c.close();return err(409,'group exists')
    ids=d['user_ids']
    if any(not isinstance(x,int) or not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone() for x in ids):c.close();return err(400,'invalid members')
    c.close()
    with core.tx() as c:
        c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],d['handle'],d['name'],u['id'],json.dumps(list(dict.fromkeys(ids)))))
        r=c.execute('SELECT * FROM groups_ WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone()
    return js({'group':group_json(r)},201)
async def group_item(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'workspace not found')
    c=core.connect(); g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone(); c.close()
    if not g:return js({'deleted':True}) if req.method=='DELETE' else err(404,'group not found')
    if role(w['id'],u['id'])!= 'owner' and g['creator_id']!=u['id']:return err(403,'forbidden')
    if req.method=='DELETE':
        with core.tx() as c:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],))
        return js({'deleted':True})
    d=await body(req)
    if not isinstance(d,dict):return err(400,'invalid group')
    name=d.get('name',g['name']); ids=d.get('user_ids',json.loads(g['user_ids']))
    if not isinstance(name,str) or not isinstance(ids,list):return err(400,'invalid group')
    with core.tx() as c:
        if any(not isinstance(x,int) or not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone() for x in ids):return err(400,'invalid members')
        c.execute('UPDATE groups_ SET name=?,user_ids=? WHERE id=?',(name,json.dumps(list(dict.fromkeys(ids))),g['id']))
        g=c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone()
    return js({'group':group_json(g)})
def routes():
    r=web.RouteTableDef()
    r.get('/api/health')(health); r.post('/api/auth/register')(register); r.post('/api/auth/login')(login); r.get('/api/auth/me')(me)
    r.get('/api/workspaces')(workspaces); r.post('/api/workspaces')(create_ws); r.get('/api/workspaces/{slug}')(workspace); r.get('/api/workspaces/{slug}/members')(members); r.post('/api/workspaces/{slug}/channels')(create_channel)
    r.get('/api/channels/{id}')(channel); r.post('/api/channels/{id}/join')(join); r.get('/api/channels/{id}/messages')(messages); r.post('/api/channels/{id}/messages')(post)
    r.patch('/api/messages/{id}')(edit); r.delete('/api/messages/{id}')(delete); r.get('/api/messages/{id}/replies')(replies); r.post('/api/messages/{id}/reactions')(react); r.delete('/api/messages/{id}/reactions/{emoji}')(unreact)
    r.patch('/api/users/me')(patch_profile); r.get('/api/ws')(ws)
    r.post('/api/dms')(missing_dm)
    r.post('/api/files')(missing_file); r.get('/api/files/{id}')(missing_file); r.get('/api/files/{id}/download')(missing_file)
    r.get('/api/users/{id}')(missing_user)
    r.get('/api/workspaces/{slug}/groups')(missing_groups); r.post('/api/workspaces/{slug}/groups')(missing_groups)
    r.patch('/api/workspaces/{slug}/groups/{handle}')(group_item); r.delete('/api/workspaces/{slug}/groups/{handle}')(group_item)
    r.get('/api/search')(search); r.get('/api/channels/{id}/read')(readstate); r.post('/api/channels/{id}/read')(markread)
    r.patch('/api/workspaces/{slug}')(patch_workspace)
    r.post('/api/workspaces/{slug}/invitations')(create_inv); r.get('/api/workspaces/{slug}/invitations')(list_inv)
    r.post('/api/invitations/{code}/accept')(accept_inv); r.delete('/api/workspaces/{slug}/invitations/{code}')(del_inv)
    r.patch('/api/workspaces/{slug}/members/{user_id}')(patch_member)
    r.post('/api/workspaces/{slug}/transfer_ownership')(transfer)
    r.get('/api/channels/{id}/members')(channel_members); r.post('/api/channels/{id}/leave')(leave)
    r.patch('/api/channels/{id}')(patch_channel)
    r.post('/api/channels/{id}/archive')(archive); r.post('/api/channels/{id}/unarchive')(archive)
    r.get('/api/channels/{id}/pins')(pins); r.post('/api/messages/{id}/pin')(pin); r.delete('/api/messages/{id}/pin')(unpin)
    return r
async def index(req):
    return web.FileResponse('/app/static/index.html')
def main():
    global NODE
    p=argparse.ArgumentParser(); p.add_argument('--node',type=int,default=0); NODE=p.parse_args().node
    app=web.Application(client_max_size=11*1024*1024); app.add_routes(routes()); app.router.add_get('/',index)
    web.run_app(app,host='127.0.0.1',port=8000+NODE)
if __name__=='__main__': main()
