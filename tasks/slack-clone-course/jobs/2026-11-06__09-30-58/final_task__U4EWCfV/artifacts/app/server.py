import asyncio, json, sys, uuid
from aiohttp import web, WSMsgType
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
def json_error(s,m): return web.json_response({'error':m},status=s)
async def read_json(req):
    try:
        x=await req.json()
        return x if isinstance(x,dict) else None
    except: return None
def require_user(req):
    h=req.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
def auth(req):
    u=require_user(req); return u if u else None
def page(req,kind):
    try:return core.parse_paging(req.query,kind)
    except: raise ValueError()
async def health(req): return web.json_response({'status':'ok','node_id':NODE})
async def register(req):
    b=await read_json(req)
    if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not isinstance(b.get('password'),str) or not __import__('re').fullmatch(r'[A-Za-z0-9_]+',b['username']) or len(b['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone(): return json_error(409,'duplicate username')
        cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
        u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone();t=core.issue_token(c,u['id'])
    return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
async def login(req):
    b=await read_json(req)
    if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not isinstance(b.get('password'),str):
        return json_error(401,'invalid credentials')
    c=core.connect()
    u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone()
    if not u or not core.verify_password(b['password'],u['password']): c.close();return json_error(401,'invalid credentials')
    t=core.issue_token(c,u['id']);c.close();return web.json_response({'user':core.user_to_json(u),'token':t})
async def me(req):
    u=auth(req)
    return web.json_response({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def workspaces(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_workspace(req):
    u=auth(req); 
    if not u:return json_error(401,'unauthorized')
    b=await read_json(req)
    if not isinstance(b,dict) or not isinstance(b.get('slug'),str) or not isinstance(b.get('name'),str) or not __import__('re').fullmatch(r'[a-z0-9-]{2,32}',b['slug']):return json_error(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return json_error(409,'duplicate slug')
        cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']));wid=cur.lastrowid
        c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(wid,u['id'],'owner'));ch=core.create_channel_row(c,wid,'general')
        core.add_channel_member(c,ch['id'],u['id'])
    return web.json_response({'workspace':core.workspace_to_json(crow(wid)),'general_channel':core.channel_to_json(ch)},status=201)
def crow(wid):
    c=core.connect();r=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();c.close();return r
async def workspace(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return json_error(403,'forbidden')
    if req.method=='PATCH':
        if role not in ('owner','admin'): return json_error(403,'forbidden')
        b=await read_json(req)
        if not isinstance(b,dict): return json_error(400,'invalid body')
        if 'name' in b and (not isinstance(b['name'],str) or not b['name'].strip() or len(b['name'])>100): return json_error(400,'invalid name')
        if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'): return json_error(400,'invalid join mode')
        with core.tx() as c:
            c.execute('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?',(b.get('name'),b.get('join_mode'),w['id']))
        return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
    include=req.query.get('include_archived') in ('1','true')
    c=core.connect()
    q='SELECT * FROM channels WHERE workspace_id=?' + ('' if include else ' AND is_archived=0')
    rs=c.execute(q,(w['id'],)).fetchall();c.close()
    return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rs],'read_state':{}})
async def members(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT m.user_id,m.role,u.username,u.display_name FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
    return web.json_response({'members':[dict(x) for x in rs]})
async def channels(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return json_error(403,'forbidden')
    b=await read_json(req)
    if not isinstance(b,dict) or not isinstance(b.get('name'),str) or not __import__('re').fullmatch(r'[a-z0-9-]{1,32}',b['name']):return json_error(400,'invalid channel')
    private=bool(b.get('is_private',False))
    if private and role not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return json_error(409,'duplicate channel')
        ch=core.create_channel_row(c,w['id'],b['name'],private,b.get('topic',''))
        core.add_channel_member(c,ch['id'],u['id'])
    return web.json_response({'channel':core.channel_to_json(ch)},status=201)
async def channel(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    action=req.match_info.get('action') or req.path.rsplit('/',1)[-1]
    if req.method=='POST' and action=='join':
        c0=core.connect(); w=c0.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
        existing=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
        if not w:return json_error(404,'not found')
        if ch['is_private'] and not existing:return json_error(403,'forbidden')
        with core.tx() as c:
            core.add_workspace_member(c,w['id'],u['id'],'member')
            core.add_channel_member(c,ch['id'],u['id'])
            core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
        return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
    if req.method=='POST' and action=='leave':
        with core.tx() as c:
            c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
            core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
        return web.json_response({'channel':core.channel_to_json(ch)})
    role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
    if not core.can_read_channel(u['id'],ch) and role!='owner':return json_error(403,'forbidden')
    if req.method=='PATCH':
        b=await read_json(req)
        if not isinstance(b,dict) or not isinstance(b.get('topic',''),str) or len(b.get('topic',''))>250:return json_error(400,'invalid topic')
        with core.tx() as c:
            c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),ch['id']))
            out=core.get_channel(ch['id']);core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(out)})
        return web.json_response({'channel':core.channel_to_json(out)})
    if action in ('archive','unarchive'):
        if role!='owner':return json_error(403,'forbidden')
        val=action=='archive'
        with core.tx() as c:
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(val),ch['id']))
            out=core.get_channel(ch['id']);core.emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':core.channel_to_json(out)})
        return web.json_response({'channel':core.channel_to_json(out)})
    return json_error(404,'not found')
async def channel_members(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    return web.json_response({'members':[core.user_to_json(x) for x in rs]})
async def messages(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    if req.method=='GET':
        try: lim,cur=page(req,'messages')
        except:return json_error(400,'invalid cursor')
        a,n=core.list_messages(ch['id'],lim,cur)
        return web.json_response({'messages':a,'next_cursor':n})
    if not core.require_writable(ch):return json_error(423,'archived')
    b=await read_json(req)
    if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty message')
    parent=b.get('parent_id')
    if parent is not None and not isinstance(parent,int):return json_error(400,'invalid parent')
    return web.json_response({'message':core.create_message(u,ch['id'],b['body'],parent)},status=201)
async def replies(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();p=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not p:return json_error(404,'not found')
    ch=core.get_channel(p['channel_id'])
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    try:lim,cur=page(req,'replies')
    except:return json_error(400,'invalid cursor')
    a,n=core.list_replies(p['id'],lim,cur)
    return web.json_response({'replies':a,'next_cursor':n})
async def reactions(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    try: mid=int(req.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    ch=core.get_channel(r['channel_id'])
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    if req.method=='POST':
        b=await read_json(req)
        if not isinstance(b,dict) or not isinstance(b.get('emoji'),str) or not b['emoji'] or len(b['emoji'])>32:return json_error(400,'invalid emoji')
        try: out=core.react(u,mid,b['emoji'],True)
        except ValueError:return json_error(404,'not found')
    else:
        emoji=req.match_info.get('emoji','')
        try: out=core.react(u,mid,emoji,False)
        except ValueError:return json_error(404,'not found')
    return web.json_response({'reactions':out})
async def edit_message(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    mid=int(req.match_info['id']); b=await read_json(req)
    with core.tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r:return json_error(404,'not found')
        if r['author_id']!=u['id']:return json_error(403,'forbidden')
        if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'invalid body')
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],core.now_iso(),mid))
        out=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();m=core.message_json(c,out)
        core.emit_event(c,r['channel_id'],'message.updated',{'message':m})
    return web.json_response({'message':m})
async def delete_message(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    mid=int(req.match_info['id'])
    with core.tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r:return web.json_response({'deleted':True})
        ch=core.get_channel(r['channel_id']);role=core.member_role(ch['workspace_id'],u['id'])
        if r['author_id']!=u['id'] and role!='owner':return json_error(403,'forbidden')
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));core.emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid})
    return web.json_response({'deleted':True})
async def ws_handler(req):
    u=core.user_for_token(req.query.get('token','')) or auth(req)
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse();await ws.prepare(req); cid=str(uuid.uuid4());core.touch_presence(cid,u['id'])
    subs={}; stop=False
    async def pump():
        while not ws.closed:
            try:
                for ch,last in list(subs.items()):
                    for f in core.events_after(ch,last):
                        await ws.send_json(f);subs[ch]=f['event_id']
                core.touch_presence(cid,u['id']);await asyncio.sleep(.05)
            except: break
    task=asyncio.create_task(pump())
    try:
        async for msg in ws:
            if msg.type!=WSMsgType.TEXT: continue
            try:b=json.loads(msg.data)
            except:continue
            ch=b.get('channel_id'); typ=b.get('type')
            if typ=='subscribe' and isinstance(ch,int):
                subs[ch]=core.head_event_id(ch)
                await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':subs[ch]})
            elif typ=='resume' and isinstance(ch,int):
                since=b.get('since_event_id',0);subs[ch]=since
                for f in core.events_after(ch,since):
                    await ws.send_json(f);subs[ch]=f['event_id']
                await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
    finally:
        task.cancel();core.drop_presence(cid)
    return ws
async def index(req):
    try:
        with open('/app/static/index.html') as f: return web.Response(text=f.read(),content_type='text/html')
    except: return web.Response(text='Huddle',content_type='text/html')
app=web.Application()
app.router.add_get('/api/health',health)
app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_workspace)
app.router.add_route('*','/api/workspaces/{slug}',workspace);app.router.add_get('/api/workspaces/{slug}/members',members);app.router.add_post('/api/workspaces/{slug}/channels',channels)
app.router.add_route('POST','/api/channels/{id}/join',channel);app.router.add_route('POST','/api/channels/{id}/leave',channel);app.router.add_route('POST','/api/channels/{id}/archive',channel);app.router.add_route('POST','/api/channels/{id}/unarchive',channel);app.router.add_route('PATCH','/api/channels/{id}',channel)
app.router.add_get('/api/channels/{id}/members',channel_members);app.router.add_route('*','/api/channels/{id}/messages',messages)
app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_post('/api/messages/{id}/reactions',reactions);app.router.add_delete('/api/messages/{id}/reactions/{emoji}',reactions);app.router.add_patch('/api/messages/{id}',edit_message);app.router.add_delete('/api/messages/{id}',delete_message)
app.router.add_get('/api/ws',ws_handler);app.router.add_get('/',index)
if __name__=='__main__':
    web.run_app(app,host='127.0.0.1',port=8000+NODE)
