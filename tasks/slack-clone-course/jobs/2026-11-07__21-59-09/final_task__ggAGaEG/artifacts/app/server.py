import asyncio, argparse, json, os, re, uuid
from aiohttp import web, WSMsgType
import core
NODE=0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(req):
    try:return await req.json()
    except:return None
def require_user(req):
    h=req.headers.get('Authorization','')
    token=h[7:] if h.startswith('Bearer ') else req.query.get('token','')
    u=core.user_for_token(token)
    if not u: raise web.HTTPUnauthorized()
    return u
def j(data,status=200): return web.json_response(data,status=status)
async def health(req): return j({'status':'ok','node_id':NODE})
async def register(req):
    b=await read_json(req)
    if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        try:
            x=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
        except: return json_error(409,'duplicate username')
        u=c.execute('SELECT * FROM users WHERE id=?',(x.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
    return j({'user':core.user_to_json(u),'token':t},201)
async def login(req):
    b=await read_json(req); c=core.connect()
    u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if isinstance(b,dict) else '',)).fetchone(); c.close()
    if not u or not isinstance(b,dict) or not core.verify_password(b.get('password',''),u['password']):return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return j({'user':core.user_to_json(u),'token':t})
async def me(req):
    try:u=require_user(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    return j({'user':core.user_to_json(u)})
def uid(req):
    return require_user(req)
async def workspaces(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    c=core.connect(); a=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return j({'workspaces':[core.workspace_to_json(x) for x in a]})
async def create_workspace(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    b=await read_json(req)
    if not isinstance(b,dict) or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')) or not isinstance(b.get('name'),str):return json_error(400,'invalid workspace')
    with core.tx() as c:
        try:
            x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']))
        except: return json_error(409,'duplicate slug')
        wid=x.lastrowid; core.add_workspace_member(c,wid,u['id'],'owner'); ch=core.create_channel_row(c,wid,'general'); core.add_channel_member(c,ch['id'],u['id'])
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()
    return j({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
async def workspace(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect(); cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall(); c.close()
    return j({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in cs],'read_state':{}})
async def create_channel(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    w=core.get_workspace(req.match_info['slug']); b=await read_json(req)
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) is None:return json_error(403,'forbidden')
    if not isinstance(b,dict) or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return json_error(400,'invalid channel')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return json_error(409,'duplicate channel')
        ch=core.create_channel_row(c,w['id'],b['name'],b.get('is_private',False),b.get('topic','')); core.add_channel_member(c,ch['id'],u['id'])
    return j({'channel':core.channel_to_json(ch)},201)
async def join(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    with core.tx() as c:
        if ch['workspace_id'] and core.member_role(ch['workspace_id'],u['id']) is None:core.add_workspace_member(c,ch['workspace_id'],u['id'])
        core.add_channel_member(c,ch['id'],u['id'])
    return j({'channel':core.channel_to_json(ch)})
async def messages(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    cid=int(req.match_info['id']); ch=core.get_channel(cid)
    if not ch:return json_error(404,'not found')
    if req.method=='GET':return j({'messages':core.list_messages(cid),'next_cursor':None})
    b=await read_json(req)
    if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty body')
    if not core.require_writable(ch):return json_error(423,'archived')
    return j({'message':core.create_message(u,cid,b['body'],b.get('parent_id'))},201)
async def replies(req):
    try:uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    return j({'replies':core.list_replies(int(req.match_info['id'])),'next_cursor':None})
async def message_edit(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    b=await read_json(req)
    if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'invalid body')
    try:return j({'message':core.edit_message(u,int(req.match_info['id']),b['body'])})
    except LookupError:return json_error(404,'not found')
    except PermissionError:return json_error(403,'forbidden')
    except RuntimeError:return json_error(409,'deleted')
async def message_delete(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    try:core.delete_message(u,int(req.match_info['id']));return j({'deleted':True})
    except LookupError:return json_error(404,'not found')
    except PermissionError:return json_error(403,'forbidden')
async def reaction(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    try:b=await req.json(); emoji=b['emoji']
    except:return json_error(400,'invalid')
    return j({'reactions':core.react(u,int(req.match_info['id']),emoji,req.method=='POST')})
async def ws(req):
    try:u=uid(req)
    except web.HTTPException as e:return json_error(e.status,'unauthorized')
    sock=web.WebSocketResponse(); await sock.prepare(req); subs={}; alive=True
    async def pump():
        while not sock.closed:
            for cid,last in list(subs.items()):
                for ev in core.events_after(cid,last):
                    await sock.send_json(ev); subs[cid]=ev['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(pump())
    try:
        async for msg in sock:
            if msg.type==WSMsgType.TEXT:
                x=json.loads(msg.data); cid=int(x.get('channel_id',0))
                if x.get('type')=='subscribe':
                    subs[cid]=core.head_event_id(cid); await sock.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
                elif x.get('type')=='resume':
                    subs[cid]=int(x.get('since_event_id',0))
                    for ev in core.events_after(cid,subs[cid]):await sock.send_json(ev);subs[cid]=ev['event_id']
                    await sock.send_json({'type':'resumed','channel_id':cid,'head_event_id':subs[cid]})
    finally:task.cancel()
    return sock
async def index(req):
    try:return web.FileResponse('/app/static/index.html')
    except:return web.Response(text='Huddle')
def app():
    a=web.Application()
    a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_route('*','/api/channels/{id}/messages',messages);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_patch('/api/messages/{id}',message_edit);a.router.add_delete('/api/messages/{id}',message_delete)
    a.router.add_post('/api/channels/{id}/join',join);a.router.add_route('*','/api/messages/{id}/reactions',reaction);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction);a.router.add_get('/api/ws',ws);a.router.add_get('/',index)
    return a
if __name__=='__main__':
    core.init_db();p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node
    web.run_app(app(),host='127.0.0.1',port=8000+NODE)
