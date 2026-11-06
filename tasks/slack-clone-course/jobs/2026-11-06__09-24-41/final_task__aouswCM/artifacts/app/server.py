import argparse, asyncio, json, re, uuid
from aiohttp import web, WSMsgType
import core

core.init_db()
NODE=0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(req):
    try:
        x=await req.json(); return x if isinstance(x,dict) else None
    except: return None
def require_user(req):
    h=req.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else req.query.get('token'))
def user_json(uid):
    c=core.connect(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return core.user_to_json(r)
def ok(x,status=200): return web.json_response(x,status=status)
def auth(req):
    u=require_user(req)
    return u if u else None
async def health(req): return ok({'status':'ok','node_id':NODE})
async def register(req):
    b=await read_json(req)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone(): return json_error(409,'username exists')
        uid=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'])).lastrowid
        t=core.issue_token(c,uid); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    return ok({'user':core.user_to_json(u),'token':t},201)
async def login(req):
    b=await read_json(req)
    if not b:return json_error(400,'invalid body')
    c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username'),)).fetchone(); c.close()
    if not u or not isinstance(b.get('password'),str) or not core.verify_password(b['password'],u['password']):return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return ok({'user':core.user_to_json(u),'token':t})
async def me(req):
    u=auth(req)
    return ok({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def workspaces(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return ok({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    b=await read_json(req)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str):return json_error(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return json_error(409,'slug exists')
        wid=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])).lastrowid
        c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'))
        ch=core.create_channel_row(c,wid,'general'); core.add_channel_member(c,ch['id'],u['id'])
    return ok({'workspace':core.workspace_to_json(crow(wid)),'general_channel':core.channel_to_json(ch)},201)
def crow(wid):
    c=core.connect(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); c.close(); return r
async def workspace(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall(); c.close()
    return ok({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in cs],'read_state':{}})
async def members(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall(); c.close()
    return ok({'members':[dict(x) for x in rs]})
async def create_channel(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return json_error(403,'forbidden')
    b=await read_json(req)
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return json_error(400,'invalid channel')
    if b.get('is_private') and role not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return json_error(409,'channel exists')
        ch=core.create_channel_row(c,w['id'],b['name'],bool(b.get('is_private')),b.get('topic','') if isinstance(b.get('topic',''),str) else '')
        core.add_channel_member(c,ch['id'],u['id'])
    return ok({'channel':core.channel_to_json(ch)},201)
async def join(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if ch['is_private']: 
        c=core.connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.close()
        if not x:return json_error(403,'private channel')
    core.join_channel(u['id'],ch['id']); return ok({'channel':core.channel_to_json(ch)})
async def leave(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    with core.tx() as c: core.remove_channel_member(c,ch['id'],u['id'])
    return ok({'channel':core.channel_to_json(ch)})
async def channel_members(req):
    u=auth(req); ch=core.get_channel(req.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return ok({'members':[core.user_to_json(x) for x in rs]})
async def messages(req):
    u=auth(req); ch=core.get_channel(req.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    try:n,cur=core.parse_paging(req.query,'messages')
    except:return json_error(400,'invalid cursor')
    rows,nc=core.list_messages(ch['id'],n,cur); return ok({'messages':rows,'next_cursor':nc})
async def post_message(req):
    u=auth(req); ch=core.get_channel(req.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if ch['is_archived']:return json_error(423,'archived')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    b=await read_json(req)
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty message')
    d=core.create_message(u['id'],ch['id'],b['body'],b.get('parent_id')); return ok({'message':d},201)
async def replies(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); rs=c.execute('SELECT m.*,u.username author_name,u.display_name FROM messages m JOIN users u ON u.id=m.author_id WHERE m.parent_id=? AND m.deleted=0 ORDER BY m.id',(req.match_info['id'],)).fetchall(); c.close()
    out=[]
    for r in rs:
        d=dict(r); d['author']={'id':d['author_id'],'username':d['author_name'],'display_name':d['display_name']}; d.update(reply_count=0,files=[],reactions=[],mentions=[]); out.append(d)
    return ok({'replies':out,'next_cursor':None})
async def ws_handler(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse(); await ws.prepare(req); subs={}; alive=True
    async def poll():
        while not ws.closed:
            for ch,last in list(subs.items()):
                for f in core.events_after(ch,last):
                    await ws.send_json(f); subs[ch]=f['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    try:
        async for msg in ws:
            if msg.type!=WSMsgType.TEXT: continue
            try:b=json.loads(msg.data)
            except:continue
            ch=b.get('channel_id')
            if b.get('type')=='subscribe' and ch:
                subs[int(ch)]=core.head_event_id(int(ch)); await ws.send_json({'type':'subscribed','channel_id':int(ch),'head_event_id':subs[int(ch)]})
            elif b.get('type')=='resume' and ch:
                subs[int(ch)]=int(b.get('since_event_id',0))
                for f in core.events_after(int(ch),subs[int(ch)]): await ws.send_json(f); subs[int(ch)]=f['event_id']
                await ws.send_json({'type':'resumed','channel_id':int(ch),'head_event_id':core.head_event_id(int(ch))})
    finally: task.cancel()
    return ws
async def root(req):
    try:return web.FileResponse('/app/static/index.html')
    except:return web.Response(text='<h1>Huddle</h1>',content_type='text/html')
def app():
    a=web.Application(); a.router.add_get('/api/health',health); a.router.add_post('/api/auth/register',register); a.router.add_post('/api/auth/login',login); a.router.add_get('/api/auth/me',me)
    a.router.add_post('/api/dms',create_dm); a.router.add_get('/api/workspaces',workspaces); a.router.add_post('/api/workspaces',create_workspace); a.router.add_get('/api/workspaces/{slug}',workspace); a.router.add_get('/api/workspaces/{slug}/members',members); a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/channels/{id}/join',join); a.router.add_post('/api/channels/{id}/leave',leave); a.router.add_get('/api/channels/{id}/members',channel_members); a.router.add_get('/api/channels/{id}/messages',messages); a.router.add_post('/api/channels/{id}/messages',post_message)
    a.router.add_get('/api/messages/{id}/replies',replies); a.router.add_post('/api/messages/{id}/pin',pin_message); a.router.add_delete('/api/messages/{id}/pin',unpin_message); a.router.add_get('/api/channels/{id}/pins',list_pins); a.router.add_patch('/api/messages/{id}',edit_msg); a.router.add_delete('/api/messages/{id}',delete_msg); a.router.add_post('/api/messages/{id}/reactions',add_reaction); a.router.add_delete('/api/messages/{id}/reactions/{emoji}',remove_reaction); a.router.add_patch('/api/users/me',patch_me); a.router.add_patch('/api/channels/{id}',patch_channel); a.router.add_post('/api/channels/{id}/archive',archive_channel); a.router.add_post('/api/channels/{id}/unarchive',unarchive_channel); a.router.add_get('/api/ws',ws_handler); a.router.add_get('/',root); return a

async def patch_channel(req):
    u=auth(req); ch=core.get_channel(req.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    b=await read_json(req)
    if not b or not isinstance(b.get('topic',''),str) or len(b.get('topic',''))>250:return json_error(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),ch['id']))
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(ch)})
    return ok({'channel':core.channel_to_json(ch)})
async def archive_channel(req):
    u=auth(req); ch=core.get_channel(req.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=1 WHERE id=?',(ch['id'],)); ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.archived',{'channel':core.channel_to_json(ch)})
    return ok({'channel':core.channel_to_json(ch)})
async def unarchive_channel(req):
    u=auth(req); ch=core.get_channel(req.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=0 WHERE id=?',(ch['id'],)); ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.unarchived',{'channel':core.channel_to_json(ch)})
    return ok({'channel':core.channel_to_json(ch)})
async def edit_msg(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    b=await read_json(req)
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'invalid body')
    d,e=core.edit_message(u['id'],int(req.match_info['id']),b['body'])
    if e:return json_error(404 if e=='not found' else 403 if e=='forbidden' else 409,e)
    return ok({'message':d})
async def delete_msg(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    good,e=core.delete_message(u['id'],int(req.match_info['id']))
    if e:return json_error(404 if e=='not found' else 403,e)
    return ok({'deleted':True})
async def add_reaction(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    b=await read_json(req)
    if not b or not isinstance(b.get('emoji'),str) or not b['emoji'] or len(b['emoji'])>32:return json_error(400,'invalid emoji')
    r=core.react(u['id'],int(req.match_info['id']),b['emoji'],True)
    if r is None:return json_error(404,'not found')
    return ok({'reactions':r})
async def remove_reaction(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    r=core.react(u['id'],int(req.match_info['id']),req.match_info['emoji'],False)
    if r is None:return json_error(404,'not found')
    return ok({'reactions':r})
async def patch_me(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    b=await read_json(req)
    if not b:return json_error(400,'invalid body')
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if any(k not in allowed or not isinstance(v,str) or len(v)>500 for k,v in b.items()):return json_error(400,'invalid profile')
    with core.tx() as c:
        for k,v in b.items(): c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
        r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
    return ok({'user':core.user_to_json(r)})


async def create_dm(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    b=await read_json(req)
    if not b or not isinstance(b.get('user_id'),int):return json_error(400,'invalid user_id')
    other=b['user_id']
    if other==u['id'] or not user_json(other):return json_error(404,'user not found')
    a,bb=sorted((u['id'],other))
    c=core.connect(); row=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bb)).fetchone() if table_exists(c,'dms') else None; c.close()
    if row:return ok({'channel_id':row['channel_id']})
    with core.tx() as c:
        ch=c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',(f'dm-{a}-{bb}',1)).lastrowid
        c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(ch,a,ch,bb))
        c.execute('INSERT INTO dms(user_a,user_b,channel_id) VALUES(?,?,?)',(a,bb,ch))
    return ok({'channel_id':ch})
def table_exists(c,name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())
async def pin_message(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    mid=int(req.match_info['id']); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()))
        r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    return ok({'pin':dict(r)})
async def unpin_message(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    mid=int(req.match_info['id'])
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
    return ok({'unpinned':True})
async def list_pins(req):
    u=auth(req); ch=core.get_channel(req.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT p.*,m.*,u.username pinned_username FROM pins p JOIN messages m ON m.id=p.message_id JOIN users u ON u.id=p.pinned_by WHERE m.channel_id=? ORDER BY p.pinned_at DESC',(ch['id'],)).fetchall(); c.close()
    out=[]
    for r in rs:
        d=dict(r); d['message']={'id':d['message_id'],'channel_id':d['channel_id'],'author_id':d['author_id'],'body':d['body'],'parent_id':d['parent_id'],'created_at':d['created_at'],'edited_at':d['edited_at'],'files':[],'reactions':[],'mentions':[]}; d['pinned_by']={'id':d['pinned_by'],'username':d['pinned_username']}; out.append(d)
    return ok({'pins':out,'next_cursor':None})

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--node',type=int,default=0); NODE=p.parse_args().node
    web.run_app(app(),host='127.0.0.1',port=8000+NODE,handle_signals=False)
