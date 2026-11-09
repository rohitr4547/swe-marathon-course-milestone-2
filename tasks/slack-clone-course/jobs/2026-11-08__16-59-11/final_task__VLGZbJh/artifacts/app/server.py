import argparse,re
from aiohttp import web
import core
p=argparse.ArgumentParser(); p.add_argument('--node',type=int,default=0); args=p.parse_args()
def err(s,m): return web.json_response({'error':m},status=s)
@web.middleware
async def json_middleware(request, handler):
    try: return await handler(request)
    except web.HTTPUnauthorized: return err(401,'unauthorized')
    except web.HTTPNotFound: return err(404,'not found')
    except web.HTTPForbidden: return err(403,'forbidden')
async def body(r):
    try:
        x=await r.json(); return x if isinstance(x,dict) else None
    except: return None
def user(r):
    h=r.headers.get('Authorization','')
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized()
    return u
def need(r):
    try:return user(r)
    except web.HTTPUnauthorized: raise
async def userget(r):
    u=need(r); c=core.connect(); x=c.execute('SELECT * FROM users WHERE id=?',(int(r.match_info['id']),)).fetchone(); c.close()
    if not x:return err(404,'not found')
    return web.json_response({'user':core.user_to_json(x)})
async def userpatch(r):
    u=need(r); d=await body(r)
    if not d:return err(400,'invalid profile')
    allowed=('display_name','timezone','avatar_url','status_text','status_emoji')
    if any(k not in allowed for k in d) or any(not isinstance(d[k],str) for k in d):return err(400,'invalid profile')
    if 'timezone' in d:
        try:
            from zoneinfo import ZoneInfo; ZoneInfo(d['timezone'])
        except: return err(400,'invalid timezone')
    limits={'display_name':80,'status_text':140,'status_emoji':32,'avatar_url':512,'timezone':64}
    for k,v in d.items():
        if len(v)>limits.get(k,250) or (k=='display_name' and not v): return err(400,'invalid profile')
    with core.tx() as c:
        for k,v in d.items():
            c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
        x=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        c.execute('INSERT INTO global_events(payload) VALUES(?)',(__import__('json').dumps({'type':'user.updated','user':core.user_to_json(x)}),))
    return web.json_response({'user':core.user_to_json(x)})

async def root(r): return web.FileResponse('/app/static/index.html')
async def health(r): return web.json_response({'status':'ok','node_id':args.node})
async def reg(r):
    d=await body(r)
    if not d or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid registration')
    try:
        with core.tx() as c:
            q=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
            u=c.execute('SELECT * FROM users WHERE id=?',(q.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
        return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
    except: return err(409,'username already exists')
async def login(r):
    d=await body(r); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username','') if d else '',)).fetchone(); c.close()
    if not u or not isinstance(d,dict) or not isinstance(d.get('password'),str) or not core.verify_password(d.get('password',''),u['password']):return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return web.json_response({'user':core.user_to_json(u),'token':t})
async def me(r): return web.json_response({'user':core.user_to_json(need(r))})
async def ws(r):
    token=r.query.get('token'); u=core.user_for_token(token) if token else None
    if not u:
        try:u=need(r)
        except:return err(401,'unauthorized')
    import asyncio,json,time,secrets
    w=web.WebSocketResponse(); await w.prepare(r); subs={}; sent={}; global_sent=0; conn_id=secrets.token_urlsafe(12)
    with core.tx() as c:c.execute('INSERT INTO presence VALUES(?,?,?)',(conn_id,u['id'],time.time()))
    async def poll():
        nonlocal global_sent
        while not w.closed:
            c=core.connect(); ge=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_sent,)).fetchall(); c.close()
            for x in ge:
                await w.send_str(x['payload']); global_sent=x['id']
            for cid in list(subs):
                c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,sent.get(cid,0))).fetchall(); c.close()
                for x in rows:
                    await w.send_str(x['payload']); sent[cid]=x['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    try:
        async for msg in w:
            if msg.type!=web.WSMsgType.TEXT: continue
            try:d=json.loads(msg.data)
            except: continue
            cid=d.get('channel_id')
            if d.get('type')=='subscribe' and isinstance(cid,int):
                ch=core.get_channel(cid)
                if not ch or not core.can_read_channel(u['id'],ch): await w.close(); break
                c=core.connect(); head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()['last_event_id']; c.close()
                subs[cid]=True; sent[cid]=head
                await w.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
            elif d.get('type')=='resume' and isinstance(cid,int):
                since=d.get('since_event_id',0); subs[cid]=True
                c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()['last_event_id']; c.close()
                for x in rows: await w.send_str(x['payload'])
                sent[cid]=head
                await w.send_json({'type':'resumed','channel_id':cid,'head_event_id':head})
    finally:
        task.cancel()
        with core.tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
    return w

async def wcreate(r):
    u=need(r); d=await body(r)
    if not d or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')) or not isinstance(d.get('name'),str) or not d['name']:return err(400,'invalid workspace')
    try:
        with core.tx() as c:
            q=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id'])); wid=q.lastrowid
            q=c.execute("INSERT INTO channels(workspace_id,name) VALUES(?, 'general')",(wid,)); gid=q.lastrowid
            c.execute('INSERT INTO members(user_id,workspace_id,channel_id,role) VALUES(?,?,NULL,?)',(u['id'],wid,'owner'))
            c.execute('INSERT INTO members(user_id,workspace_id,channel_id,role) VALUES(?,?,?,?)',(u['id'],wid,gid,'member'))
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); g=c.execute('SELECT * FROM channels WHERE id=?',(gid,)).fetchone()
        return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(g)},status=201)
    except: return err(409,'duplicate workspace')
async def wlist(r):
    u=need(r); c=core.connect(); rows=c.execute('SELECT DISTINCT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def wpatch(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if u['id']!=w['owner_id'] and core.member_role(w['id'],u['id'])!='admin':return err(403,'forbidden')
    d=await body(r)
    if not d:return err(400,'invalid body')
    with core.tx() as c:
        if 'name' in d:
            if not isinstance(d['name'],str) or not d['name']: return err(400,'invalid name')
            c.execute('UPDATE workspaces SET name=? WHERE id=?',(d['name'],w['id']))
        if 'join_mode' in d:
            if d['join_mode'] not in ('open','invite_only'):return err(400,'invalid join mode')
            c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(d['join_mode'],w['id']))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return web.json_response({'workspace':core.workspace_to_json(w)})
async def transfer(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if u['id']!=w['owner_id']:return err(403,'forbidden')
    d=await body(r)
    if not d or not isinstance(d.get('new_owner_id'),int):return err(400,'invalid owner')
    c=core.connect(); x=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=? AND channel_id IS NULL',(w['id'],d['new_owner_id'])).fetchone(); c.close()
    if not x or x['role']!='admin':return err(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=? AND channel_id IS NULL',('admin',w['id'],u['id']))
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=? AND channel_id IS NULL',('owner',w['id'],d['new_owner_id']))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(d['new_owner_id'],w['id']))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def memberpatch(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    target=int(r.match_info['user_id'])
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return err(403,'forbidden')
    c=core.connect(); x=c.execute('SELECT user_id FROM members WHERE workspace_id=? AND user_id=? AND channel_id IS NULL',(w['id'],target)).fetchone(); c.close()
    if not x:return err(404,'not found')
    d=await body(r)
    if not d or d.get('role') not in ('owner','admin','member','guest') or d.get('role')=='owner':return err(400,'invalid role')
    if target==w['owner_id'] or (role=='admin' and core.member_role(w['id'],target) in ('owner','admin')):return err(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=? AND channel_id IS NULL',(d['role'],w['id'],target))
    return web.json_response({'member':{'user_id':target,'role':d['role']}})
async def invitations(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return err(403,'forbidden')
    if r.method=='GET':
        import time
        c=core.connect(); rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND (expires_at IS NULL OR expires_at>?) AND uses<max_uses',(w['id'],time.time())).fetchall(); c.close()
        return web.json_response({'invitations':[dict(x) for x in rows]})
    d=await body(r) or {}
    if not isinstance(d.get('max_uses',1),int) or not isinstance(d.get('expires_in_seconds',0),(int,float)) or (d.get('invited_user_id') is not None and not isinstance(d.get('invited_user_id'),int)) or (d.get('invited_username') is not None and not isinstance(d.get('invited_username'),str)): return err(400,'invalid invitation')
    import time,secrets
    maxu=d.get('max_uses',1); target=d.get('invited_user_id')
    if target is None and d.get('invited_username'):
        cc=core.connect(); z=cc.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone(); cc.close(); target=z['id'] if z else None
    if d.get('invited_username') and target is None:return err(400,'unknown invited user')
    if target is not None:maxu=1
    with core.tx() as c:
        q=c.execute('INSERT INTO invitations(workspace_id,code,created_by,max_uses,expires_at,invited_user_id) VALUES(?,?,?,?,?,?)',(w['id'],secrets.token_urlsafe(12),u['id'],maxu,(time.time()+d['expires_in_seconds']) if d.get('expires_in_seconds') else None,target))
        row=c.execute('SELECT * FROM invitations WHERE id=?',(q.lastrowid,)).fetchone()
    return web.json_response({'invitation':dict(row)},status=201)
async def accept_inv(r):
    u=need(r); import time
    c=core.connect(); x=c.execute('SELECT * FROM invitations WHERE code=? AND revoked=0',(r.match_info['code'],)).fetchone(); c.close()
    if not x or (x['expires_at'] and x['expires_at']<=time.time()) or x['uses']>=x['max_uses']:return err(404,'not found')
    if x['invited_user_id'] is not None and x['invited_user_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as c:
        core.add_workspace_member(c,x['workspace_id'],u['id'],'member'); c.execute('UPDATE invitations SET uses=uses+1 WHERE id=?',(x['id'],))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace_by_id(x['workspace_id'])) if hasattr(core,'get_workspace_by_id') else core.workspace_to_json(core.connect().execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone())})
async def invdelete(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    with core.tx() as c:
        q=c.execute('UPDATE invitations SET revoked=1 WHERE workspace_id=? AND code=? AND revoked=0',(w['id'],r.match_info['code']))
        if not q.rowcount:return err(404,'not found')
    return web.json_response({'deleted':True})
async def wmembers(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT user_id,role FROM members WHERE workspace_id=? AND channel_id IS NULL',(w['id'],)).fetchall(); c.close()
    return web.json_response({'members':[dict(x) for x in rows]})
async def wdetail(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],r.query.get('include_archived') in ('1','true'))).fetchall(); c.close()
    rows=[x for x in rows if not x['is_private'] or core.can_read_channel(u['id'],x)]
    states=[]
    for x in rows:
        c0=core.connect(); q0=c0.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone(); v0=q0['last_read_event_id'] if q0 else 0; ev0=c0.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>?',(x['id'],v0)).fetchall(); c0.close(); import json; parsed=[json.loads(z['payload']) for z in ev0]; posts=[z for z in parsed if z.get('type') in ('message.created','message.reply') and z.get('message',{}).get('author_id')!=u['id']]; states.append({'channel_id':x['id'],'last_read_event_id':v0,'unread_count':len(posts),'mention_count':sum(1 for z in posts if u['id'] in z.get('message',{}).get('mentions',[]))})
    return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':states})
async def ccreate(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return err(403,'forbidden')
    d=await body(r); name=d.get('name') if d else None; priv=bool(d.get('is_private',False)) if d else False; topic=d.get('topic','') if d else ''
    if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',name) or not isinstance(topic,str) or len(topic)>250:return err(400,'invalid channel')
    if role=='guest' or (priv and role not in ('owner','admin')):return err(403,'forbidden')
    c=core.connect()
    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone():c.close(); return err(409,'duplicate channel')
    c.close()
    with core.tx() as c:
        ch=core.create_channel_row(c,w['id'],name,priv,topic)
        core.add_channel_member(c,ch['id'],u['id'])
    return web.json_response({'channel':core.channel_to_json(ch)},status=201)
async def join(r):
    u=need(r); ch=core.get_channel(int(r.match_info['id']))
    if not ch:return err(404,'not found')
    if ch['is_private']:return err(403,'forbidden')
    if ch['workspace_id'] and core.member_role(ch['workspace_id'],u['id'])=='guest': return err(403,'forbidden')
    if ch['workspace_id'] and core.member_role(ch['workspace_id'],u['id']) is None:
        w=core.connect().execute('SELECT join_mode FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
        if not w or w['join_mode']!='open': return err(403,'forbidden')
    before=core.connect().execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
    if not core.join_channel(u,ch['id']):return err(403,'forbidden')
    if not before:
        with core.tx() as c: core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def reaction(r):
    u=need(r); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(r.match_info['id']),)).fetchone(); c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if r.method=='POST':
        d=await body(r); e=d.get('emoji') if d else None
        if not isinstance(e,str) or not e:return err(400,'invalid emoji')
        with core.tx() as c:
            before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],e)).fetchone(); c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?,?)',(m['id'],u['id'],e,core.now_iso()))
            if not before: core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':m['id'],'emoji':e,'user_id':u['id']})
        return web.json_response({'reactions':msgjson(m)['reactions']})
    e=__import__('urllib.parse').parse.unquote(r.match_info['emoji'])
    with core.tx() as c:
        before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],e)).fetchone(); c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],e))
        if before: core.emit_event(c,m['channel_id'],'reaction.removed',{'message_id':m['id'],'emoji':e,'user_id':u['id']})
    return web.json_response({'reactions':msgjson(m)['reactions']})

async def groups(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    if r.method=='GET':
        c=core.connect(); rows=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
        return web.json_response({'groups':[dict(x) for x in rows]})
    d=await body(r)
    if not d or not isinstance(d.get('handle'),str) or not __import__('re').fullmatch(r'[a-z0-9-]{2,32}',d.get('handle','')) or not isinstance(d.get('name'),str) or not isinstance(d.get('user_ids',[]),list):return err(400,'invalid group')
    import json
    c=core.connect(); dup=c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(w['id'],d['handle'])).fetchone(); valid=c.execute('SELECT user_id FROM members WHERE workspace_id=? AND channel_id IS NULL',(w['id'],)).fetchall(); c.close()
    if dup:return err(409,'duplicate handle')
    allowed={z['user_id'] for z in valid}
    if any(not isinstance(z,int) or z not in allowed for z in d.get('user_ids',[])):return err(400,'invalid members')
    with core.tx() as c:
        q=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],d['handle'],d['name'],u['id'],json.dumps(d.get('user_ids',[]))))
    return web.json_response({'group':{'id':q.lastrowid,'handle':d['handle'],'name':d['name'],'member_user_ids':d.get('user_ids',[])}},status=201)

async def groupitem(r):
    u=need(r); w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone(); c.close()
    if not g:
        if r.method=='DELETE' and core.member_role(w['id'],u['id']): return web.json_response({'deleted':True})
        return err(404,'not found')
    import json
    if u['id']!=g['creator_id'] and u['id']!=w['owner_id']:return err(403,'forbidden')
    if r.method=='DELETE':
        with core.tx() as c:c.execute('DELETE FROM groups WHERE id=?',(g['id'],))
        return web.json_response({'deleted':True})
    d=await body(r)
    if not d:return err(400,'invalid group')
    import json
    with core.tx() as c:
        if 'name' in d and not isinstance(d['name'],str):return err(400,'invalid group')
        if 'name' in d:c.execute('UPDATE groups SET name=? WHERE id=?',(d['name'],g['id']))
        if 'user_ids' in d:
            if not isinstance(d['user_ids'],list):return err(400,'invalid group')
            c.execute('UPDATE groups SET user_ids=? WHERE id=?',(json.dumps(d['user_ids']),g['id']))
        g=c.execute('SELECT * FROM groups WHERE id=?',(g['id'],)).fetchone()
    return web.json_response({'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':json.loads(g['user_ids'])}})

async def dms(r):
    u=need(r); d=await body(r)
    if not d or not isinstance(d.get('user_id'),int) or isinstance(d.get('user_id'),bool):return err(400,'invalid dm')
    other=d['user_id']; c=core.connect(); target=c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone(); c.close()
    if not target:return err(404,'not found')
    a1,b1=sorted((u['id'],other))
    with core.tx() as c:
        x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a1,b1)).fetchone()
        if x: cid=x['channel_id']
        else:
            q=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)'); cid=q.lastrowid
            c.execute('INSERT INTO dms VALUES(?,?,?)',(a1,b1,cid))
            c.execute('INSERT INTO members(user_id,channel_id,role) VALUES(?,?,?)',(a1,cid,'member'))
            c.execute('INSERT INTO members(user_id,channel_id,role) VALUES(?,?,?)',(b1,cid,'member'))
    return web.json_response({'channel_id':cid})
async def fileget(r):
    u=need(r); c=core.connect(); x=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone(); c.close()
    if not x:return err(404,'not found')
    if r.path.endswith('/download'):
        return web.Response(body=open('/app/data/files/'+str(x['id']),'rb').read(),content_type=x['content_type'],headers={'Content-Disposition':'attachment; filename="'+x['filename']+'"'} )
    return web.json_response({'file':dict(x)})
async def files(r):
    u=need(r)
    try:
        form=await r.post(); f=form.get('file')
        if not f or not hasattr(f,'file'):return err(400,'file upload required')
        data=f.file.read()
    except:return err(400,'file upload required')
    if len(data)>10*1024*1024:return err(413,'file too large')
    import os
    os.makedirs('/app/data/files',exist_ok=True)
    with core.tx() as c:
        q=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],f.filename,f.content_type or 'application/octet-stream',len(data),core.now_iso())); fid=q.lastrowid
    open('/app/data/files/'+str(fid),'wb').write(data)
    return web.json_response({'file':{'id':fid,'uploader_id':u['id'],'filename':f.filename,'content_type':f.content_type or 'application/octet-stream','size':len(data),'created_at':core.now_iso()}},status=201)

async def pin(r):
    u=need(r); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(r.match_info['id']),)).fetchone(); c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    with core.tx() as c:
        x=c.execute('SELECT * FROM pins WHERE message_id=?',(m['id'],)).fetchone()
        if not x:
            t=core.now_iso(); c.execute('INSERT INTO pins VALUES(?,?,?)',(m['id'],u['id'],t)); x=c.execute('SELECT * FROM pins WHERE message_id=?',(m['id'],)).fetchone()
    return web.json_response({'pin':dict(x)})
async def unpin(r):
    u=need(r); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(int(r.match_info['id']),)).fetchone(); c.close()
    if not m:return err(404,'not found')
    if not core.can_read_channel(u['id'],core.get_channel(m['channel_id'])):return err(403,'forbidden')
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(m['id'],))
    return web.json_response({'unpinned':True})

async def readstate(r):
    u=need(r); ch=core.get_channel(int(r.match_info['id']))
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    v=0
    if r.method=='POST':
        d=await body(r)
        if not d or not isinstance(d.get('last_read_event_id'),int) or isinstance(d.get('last_read_event_id'),bool):return err(400,'invalid read state')
        with core.tx() as c:
            old=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()
            v=max(old['last_read_event_id'] if old else 0,d['last_read_event_id'])
            c.execute('INSERT OR REPLACE INTO read_states(user_id,channel_id,last_read_event_id) VALUES(?,?,?)',(u['id'],ch['id'],v))
    else:
        c=core.connect(); x=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone(); c.close()
        v=x['last_read_event_id'] if x else 0
    return web.json_response({'read_state':{'channel_id':ch['id'],'last_read_event_id':v,'unread_count':sum(1 for z in core.connect().execute('SELECT payload FROM events WHERE channel_id=? AND event_id>?',(ch['id'],v)).fetchall() if __import__('json').loads(z['payload']).get('type') in ('message.created','message.reply') and __import__('json').loads(z['payload']).get('message',{}).get('author_id')!=u['id']),'mention_count':sum(1 for z in core.connect().execute('SELECT payload FROM events WHERE channel_id=? AND event_id>?',(ch['id'],v)).fetchall() if u['id'] in __import__('json').loads(z['payload']).get('message',{}).get('mentions',[]) and __import__('json').loads(z['payload']).get('message',{}).get('author_id')!=u['id'])}})

async def pins(r):
    u=need(r); ch=core.get_channel(int(r.match_info['id']))
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    try: lim=int(r.query.get('limit','50'))
    except:return err(400,'invalid limit')
    if lim<1 or lim>200:return err(400,'invalid limit')
    cur=r.query.get('cursor'); before=None
    if cur:
        try:
            import base64,json; z=json.loads(base64.urlsafe_b64decode(cur+'===').decode()); before=int(z['v'])
        except:return err(400,'invalid cursor')
    c=core.connect();q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';args=[ch['id']]
    if before:q+=' AND p.message_id<?';args.append(before)
    q+=f' ORDER BY p.message_id DESC LIMIT {lim+1}'; rows=c.execute(q,args).fetchall();c.close();more=len(rows)>lim;rows=rows[:lim]; nxt=None
    if more:
        import base64,json;nxt=base64.urlsafe_b64encode(json.dumps({'v':rows[-1]['message_id']}).encode()).decode().rstrip('=')
    return web.json_response({'pins':[{'message':msgjson(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':nxt})

async def cmembers(r):
    u=need(r); ch=core.get_channel(int(r.match_info['id']))
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT DISTINCT u.* FROM users u JOIN members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return web.json_response({'members':[core.user_to_json(x) for x in rows]})
def msgjson(x):
    c=core.connect(); au=c.execute('SELECT * FROM users WHERE id=?',(x['author_id'],)).fetchone(); rc=c.execute("SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0 AND trim(body)<>''",(x['id'],)).fetchone()['n']
    import re
    members=c.execute('SELECT u.id,u.username FROM users u JOIN members mm ON mm.user_id=u.id WHERE mm.workspace_id=(SELECT workspace_id FROM channels WHERE id=?)',(x['channel_id'],)).fetchall()
    mentions=[]
    if '@channel' in x['body']:
        mentions += [z['user_id'] for z in c.execute('SELECT user_id FROM members WHERE channel_id=?',(x['channel_id'],)).fetchall() if z['user_id']!=x['author_id']]
    if '@here' in x['body']:
        mentions += [z['user_id'] for z in c.execute("SELECT DISTINCT m.user_id FROM members m JOIN presence p ON p.user_id=m.user_id WHERE m.channel_id=? AND m.user_id!=? AND p.last_seen>?",(x['channel_id'],x['author_id'],__import__('time').time()-3)).fetchall() if z['user_id'] not in mentions]
    for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',x['body']):
        for z in members:
            if z['username']==name and z['id']!=x['author_id'] and z['id'] not in mentions: mentions.append(z['id'])
    for name in re.findall(r'(?<![A-Za-z0-9_])@([a-z0-9-]{2,32})',x['body']):
        for g in c.execute('SELECT user_ids FROM groups WHERE workspace_id=(SELECT workspace_id FROM channels WHERE id=?) AND handle=?',(x['channel_id'],name)).fetchall():
            for uid in __import__('json').loads(g['user_ids']):
                if uid!=x['author_id'] and c.execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(x['channel_id'],uid)).fetchone() and uid not in mentions: mentions.append(uid)
    rr=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(x['id'],)).fetchall(); reacts=[]
    for z in rr:
        q=next((a for a in reacts if a['emoji']==z['emoji']),None)
        if not q:q={'emoji':z['emoji'],'count':0,'user_ids':[]};reacts.append(q)
        q['count']+=1;q['user_ids'].append(z['user_id'])
    return {'id':x['id'],'channel_id':x['channel_id'],'author_id':x['author_id'],'author':core.user_to_json(au),'body':x['body'],'parent_id':x['parent_id'],'reply_count':rc,'created_at':x['created_at'],'edited_at':x['edited_at'],'files':[dict(z) for z in c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE message_id=?',(x['id'],)).fetchall()],'reactions':reacts,'mentions':mentions}
    c.close()
async def messages(r):
    u=need(r); ch=core.get_channel(int(r.match_info['id']))
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if 'page' in r.query or 'offset' in r.query:return err(400,'offset pagination unsupported')
    try: lim=int(r.query.get('limit','50'))
    except:return err(400,'invalid limit')
    if lim<1 or lim>200:return err(400,'invalid limit')
    before=None; cur=r.query.get('cursor')
    if cur:
        import base64,json
        try:
            z=json.loads(base64.urlsafe_b64decode(cur+'===').decode())
            if z.get('k')!='messages' or not isinstance(z.get('v'),int):raise ValueError()
            before=z['v']
        except:return err(400,'invalid cursor')
    if r.method=='GET':
        c=core.connect(); q="SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 AND trim(body)<>''"; args=[ch['id']]
        if before is not None:q+=' AND id<?';args.append(before)
        q+=f' ORDER BY id DESC LIMIT {lim+1}'; rows=c.execute(q,args).fetchall();c.close()
        more=len(rows)>lim; rows=rows[:lim]; nxt=None
        if more:
            import base64,json
            nxt=base64.urlsafe_b64encode(json.dumps({'k':'messages','v':rows[-1]['id']}).encode()).decode().rstrip('=')
        return web.json_response({'messages':[msgjson(x) for x in rows],'next_cursor':nxt})
    d=await body(r)
    if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'invalid message')
    raw=d['body']
    if raw.startswith('//'): d['body']=raw[1:]
    elif raw.startswith('/me'):
        if raw=='/me' or not raw.startswith('/me ') or not raw[4:].strip():return err(400,'invalid command')
    elif raw.startswith('/shrug'):
        d['body']=(raw[7:].strip()+' ' if raw[7:].strip() else '')+'¯\\_(ツ)_/¯'
    elif raw.startswith('/invite '):
        import re
        m=re.fullmatch(r'/invite +@([A-Za-z0-9_]+)',raw)
        if not m:return err(400,'invalid command')
        c0=core.connect(); target=c0.execute('SELECT * FROM users WHERE username=?',(m.group(1),)).fetchone(); c0.close()
        if not target:return err(400,'unknown user')
        with core.tx() as c:
            core.add_channel_member(c,ch['id'],target['id'])
        return web.json_response({'message':None,'invited_user':core.user_to_json(target)},status=201)
    elif raw.startswith('/topic '):
        topic=raw[7:]
        with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']))
        return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
    elif raw in ('/archive','/unarchive'):
        w=core.get_workspace_by_id(ch['workspace_id'])
        if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
        flag=raw=='/archive'
        with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(flag),ch['id']))
        return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
    elif raw.startswith('/') and not raw.startswith('//'): return err(400,'unknown command')
    if ch['is_archived']:return err(423,'archived')
    parent=d.get('parent_id')
    if parent is not None and not isinstance(parent,int): return err(400,'invalid parent')
    file_ids=d.get('file_ids',[])
    if not isinstance(file_ids,list) or any(not isinstance(z,int) or isinstance(z,bool) for z in file_ids): return err(400,'invalid files')
    if len(file_ids)!=len(set(file_ids)): return err(400,'invalid files')
    cf=core.connect()
    for fid in file_ids:
        z=cf.execute('SELECT uploader_id,message_id FROM files WHERE id=?',(fid,)).fetchone()
        if not z or z['uploader_id']!=u['id'] or z['message_id'] is not None:
            cf.close(); return err(400,'invalid files')
    cf.close()
    with core.tx() as c:
        q=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],d['body'],parent,core.now_iso()));
        if file_ids: c.executemany('UPDATE files SET message_id=? WHERE id=?',[(q.lastrowid,z) for z in file_ids])
        x=c.execute('SELECT * FROM messages WHERE id=?',(q.lastrowid,)).fetchone(); core.emit_event(c,ch['id'],'message.reply' if parent else 'message.created',{'message':msgjson(x)})
    return web.json_response({'message':msgjson(x)},status=201)

async def message_edit(r):
    u=need(r); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(int(r.match_info['id']),)).fetchone(); c.close()
    if not m:return err(404,'not found')
    if m['deleted']:return err(409,'message deleted')
    if m['author_id']!=u['id']:return err(403,'forbidden')
    d=await body(r)
    if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'invalid message')
    with core.tx() as c:
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],core.now_iso(),m['id']))
        x=c.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone()
        core.emit_event(c,m['channel_id'],'message.updated',{'message':msgjson(x)})
    return web.json_response({'message':msgjson(x)})
async def message_delete(r):
    u=need(r); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(int(r.match_info['id']),)).fetchone(); c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id']); w=core.get_workspace_by_id(ch['workspace_id']) if ch['workspace_id'] else None
    if m['deleted']:return web.json_response({'deleted':True})
    if m['author_id']!=u['id'] and (not w or w['owner_id']!=u['id']):return err(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m['id'],))
        core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':m['id']})
    return web.json_response({'deleted':True})
async def search(r):
    u=need(r); text=r.query.get('q')
    if not text:return err(400,'q required')
    try: lim=int(r.query.get('limit','50'))
    except:return err(400,'invalid limit')
    if lim<1 or lim>200:return err(400,'invalid limit')
    if 'page' in r.query or 'offset' in r.query:return err(400,'offset pagination unsupported')
    before=None;cur=r.query.get('cursor')
    if cur:
        try:
            import base64,json;z=json.loads(base64.urlsafe_b64decode(cur+'===').decode())
            if z.get('k')!='search' or not isinstance(z.get('v'),int):raise ValueError()
            before=z['v']
        except:return err(400,'invalid cursor')
    ws=r.query.get('workspace_id')
    if ws:
        try: ws=int(ws)
        except:return err(400,'invalid workspace_id')
    c=core.connect(); q='SELECT m.* FROM messages m WHERE m.deleted=0 AND lower(m.body) LIKE lower(?)';args=['%'+text+'%']
    if before is not None:q+=' AND m.id<?';args.append(before)
    q+=' ORDER BY m.id DESC'; rows=c.execute(q,args).fetchall();c.close()
    rows=[x for x in rows if (not ws or core.get_channel(x['channel_id'])['workspace_id']==ws) and core.can_read_channel(u['id'],core.get_channel(x['channel_id']))]
    more=len(rows)>lim; rows=rows[:lim]; nxt=None
    if more:
        import base64,json;nxt=base64.urlsafe_b64encode(json.dumps({'k':'search','v':rows[-1]['id']}).encode()).decode().rstrip('=')
    return web.json_response({'results':[msgjson(x) for x in rows],'next_cursor':nxt})

async def replies(r):
    u=need(r); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(r.match_info['id']),)).fetchone(); c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if 'page' in r.query or 'offset' in r.query:return err(400,'offset pagination unsupported')
    try: lim=int(r.query.get('limit','50'))
    except:return err(400,'invalid limit')
    if lim<1 or lim>200:return err(400,'invalid limit')
    after=None; cur=r.query.get('cursor')
    if cur:
        import base64,json
        try:
            z=json.loads(base64.urlsafe_b64decode(cur+'===').decode())
            if not isinstance(z,dict) or z.get('k')!='replies' or not isinstance(z.get('v'),int):raise ValueError()
            after=z['v']
        except:return err(400,'invalid cursor')
    c=core.connect(); q="SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND trim(body)<>''"; args=[m['id']]
    if after is not None:q+=' AND id>?';args.append(after)
    q+=f' ORDER BY id ASC LIMIT {lim+1}'; rows=c.execute(q,args).fetchall(); c.close()
    more=len(rows)>lim; rows=rows[:lim]; nxt=None
    if more:
        import base64,json
        nxt=base64.urlsafe_b64encode(json.dumps({'k':'replies','v':rows[-1]['id']}).encode()).decode().rstrip('=')
    return web.json_response({'replies':[msgjson(x) for x in rows],'next_cursor':nxt})

async def cpatch(r):
    u=need(r); ch=core.get_channel(int(r.match_info['id']))
    if not ch:return err(404,'not found')
    if ch['is_archived']:return err(423,'archived')
    c0=core.connect()
    owner=bool(ch['workspace_id'] and c0.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() and c0.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()['owner_id']==u['id'])
    member=bool(c0.execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone())
    c0.close()
    if not (owner or member): return err(403,'forbidden')
    d=await body(r)
    if not d or not isinstance(d.get('topic'),str) or len(d['topic'])>250:return err(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],ch['id']))
        core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def cleave(r):
    u=need(r); ch=core.get_channel(int(r.match_info['id']))
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    with core.tx() as c:
        removed=core.remove_channel_member(c,ch['id'],u['id'])
        if removed: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def carchive(r):
    u=need(r); ch=core.get_channel(int(r.match_info['id']))
    if not ch:return err(404,'not found')
    w=core.get_workspace_by_id(ch['workspace_id']) if ch['workspace_id'] else None
    if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
    flag=r.path.endswith('/archive')
    with core.tx() as c:
        old=core.get_channel(ch['id'])['is_archived']; c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(flag),ch['id']))
        if bool(old)!=flag: core.emit_event(c,ch['id'],'channel.archived' if flag else 'channel.unarchived',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})

app=web.Application(middlewares=[json_middleware],client_max_size=11*1024*1024)
app.router.add_get('/',root); app.router.add_get('/api/health',health); app.router.add_get('/api/users/{id}',userget); app.router.add_patch('/api/users/me',userpatch); app.router.add_post('/api/auth/register',reg); app.router.add_post('/api/auth/login',login); app.router.add_get('/api/auth/me',me); app.router.add_get('/api/ws',ws)
app.router.add_post('/api/workspaces',wcreate); app.router.add_post('/api/dms',dms); app.router.add_get('/api/workspaces/{slug}/groups',groups); app.router.add_post('/api/workspaces/{slug}/groups',groups); app.router.add_patch('/api/workspaces/{slug}/groups/{handle}',groupitem); app.router.add_delete('/api/workspaces/{slug}/groups/{handle}',groupitem); app.router.add_post('/api/workspaces/{slug}/groups',groups); app.router.add_post('/api/files',files); app.router.add_get('/api/files/{id}',fileget); app.router.add_get('/api/files/{id}/download',fileget); app.router.add_patch('/api/workspaces/{slug}',wpatch); app.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer); app.router.add_get('/api/workspaces/{slug}/members',wmembers); app.router.add_get('/api/workspaces/{slug}/invitations',invitations); app.router.add_post('/api/workspaces/{slug}/invitations',invitations); app.router.add_post('/api/invitations/{code}/accept',accept_inv); app.router.add_delete('/api/workspaces/{slug}/invitations/{code}',invdelete); app.router.add_patch('/api/workspaces/{slug}/members/{user_id}',memberpatch); app.router.add_get('/api/workspaces',wlist); app.router.add_get('/api/workspaces/{slug}',wdetail); app.router.add_post('/api/workspaces/{slug}/channels',ccreate)
app.router.add_post('/api/channels/{id}/join',join); app.router.add_get('/api/channels/{id}/pins',pins); app.router.add_get('/api/channels/{id}/read',readstate); app.router.add_post('/api/channels/{id}/read',readstate); app.router.add_get('/api/messages/{id}/replies',replies); app.router.add_patch('/api/messages/{id}',message_edit); app.router.add_delete('/api/messages/{id}',message_delete); app.router.add_post('/api/messages/{id}/reactions',reaction); app.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction); app.router.add_post('/api/messages/{id}/pin',pin); app.router.add_delete('/api/messages/{id}/pin',unpin); app.router.add_get('/api/search',search); app.router.add_get('/api/channels/{id}/messages',messages); app.router.add_post('/api/channels/{id}/messages',messages); app.router.add_get('/api/channels/{id}/members',cmembers); app.router.add_patch('/api/channels/{id}',cpatch); app.router.add_post('/api/channels/{id}/leave',cleave); app.router.add_get('/api/channels/{id}/members',cmembers); app.router.add_post('/api/channels/{id}/archive',carchive); app.router.add_post('/api/channels/{id}/unarchive',carchive)
web.run_app(app,host='127.0.0.1',port=8000+args.node)

