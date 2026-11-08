import asyncio,json,argparse,uuid,os,re
from aiohttp import web,WSMsgType
import core
NODE=0
def json_error(s,m): return web.json_response({'error':m},status=s)
async def read_json(r):
    try:
        x=await r.json()
        return x if isinstance(x,dict) else None
    except:return None
def require_user(r):
    h=r.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
def ok(x,status=200): return web.json_response(x,status=status)
async def auth(r):
    b=await read_json(r)
    if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not isinstance(b.get('password'),str):return json_error(400,'invalid')
    u=b['username'];p=b['password']
    if r.path.endswith('/register'):
        if not re.fullmatch(r'[A-Za-z0-9_]+',u) or len(p)<8:return json_error(400,'invalid')
        with core.tx() as c:
            if c.execute('SELECT 1 FROM users WHERE username=?',(u,)).fetchone():return json_error(409,'exists')
            x=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,core.hash_password(p),b.get('display_name') or u));uid=x.lastrowid;t=core.issue_token(c,uid);user=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        return ok({'user':core.user_to_json(user),'token':t},201)
    c=core.connect();user=c.execute('SELECT * FROM users WHERE username=?',(u,)).fetchone();c.close()
    if not user or not core.verify_password(p,user['password']):return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,user['id'])
    return ok({'user':core.user_to_json(user),'token':t})
async def me(r):
    u=require_user(r)
    return json_error(401,'unauthorized') if not u else ok({'user':core.user_to_json(u)})
async def health(r):return ok({'status':'ok','node_id':NODE})
async def workspaces(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    if r.method=='POST':
        b=await read_json(r)
        if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str):return json_error(400,'invalid')
        try:
            with core.tx() as c:
                x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']));wid=x.lastrowid
                c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(wid,u['id'],'owner'));g=core.create_channel_row(c,wid,'general');c.execute('INSERT INTO channel_members VALUES(?,?)',(g['id'],u['id']))
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()
            return ok({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(g)},201)
        except: return json_error(409,'exists')
    c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],));a=[core.workspace_to_json(x) for x in rs];c.close();return ok({'workspaces':a})
async def workspace(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(r.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect();chs=[core.channel_to_json(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],))];ms=[dict(x) for x in c.execute('SELECT user_id,role FROM workspace_members WHERE workspace_id=?',(w['id'],))];c.close()
    return ok({'workspace':core.workspace_to_json(w),'channels':chs,'read_state':{}})
async def channels(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(r.match_info['slug'])
    if not w:return json_error(404,'not found')
    if r.method=='POST':
        b=await read_json(r)
        if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return json_error(400,'invalid')
        try:
            with core.tx() as c:
                x=core.create_channel_row(c,w['id'],b['name'],bool(b.get('is_private',False)),b.get('topic',''));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(x['id'],u['id']));return ok({'channel':core.channel_to_json(x)},201)
        except:return json_error(409,'exists')
async def join(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(r.match_info['id']))
    if not ch:return json_error(404,'not found')
    core.join_channel(u,ch['id']);return ok({'channel':core.channel_to_json(ch)})
async def messages(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    cid=int(r.match_info['id']);ch=core.get_channel(cid)
    if not ch:return json_error(404,'not found')
    if r.method=='GET':
        try:n,cur=core.parse_paging(r.query,'messages');a,x=core.list_messages(cid,n,cur);return ok({'messages':a,'next_cursor':x})
        except:return json_error(400,'invalid cursor')
    b=await read_json(r)
    if not b or not isinstance(b.get('body'),str):return json_error(400,'invalid')
    try:m,f=core.create_message(u,cid,b['body'],b.get('parent_id'));return ok({'message':m},201)
    except PermissionError:return json_error(403,'forbidden')
    except RuntimeError:return json_error(423,'archived')
    except:return json_error(400,'invalid')
async def replies(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    try:n,cur=core.parse_paging(r.query,'replies');a,x=core.list_replies(int(r.match_info['id']),n,cur);return ok({'replies':a,'next_cursor':x})
    except:return json_error(400,'invalid')
async def ws_handler(r):
    u=require_user(type('R',(),{'headers':{'Authorization':'Bearer '+r.query.get('token','')}})())
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse();await ws.prepare(r);subs={};last={}
    async def pump():
        while not ws.closed:
            for cid,s in list(subs.items()):
                for e in core.events_after(cid,last.get(cid,s)):
                    await ws.send_json(e);last[cid]=e['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(pump())
    try:
        async for msg in ws:
            if msg.type!=WSMsgType.TEXT:continue
            try:b=json.loads(msg.data)
            except:continue
            cid=int(b.get('channel_id',0))
            if b.get('type')=='subscribe':
                subs[cid]=core.head_event_id(cid);last[cid]=subs[cid];await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
            elif b.get('type')=='resume':
                since=int(b.get('since_event_id',0));subs[cid]=since;last[cid]=since
                for e in core.events_after(cid,since):await ws.send_json(e);last[cid]=e['event_id']
                await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':core.head_event_id(cid)})
    finally:task.cancel()
    return ws
async def index(r):
    return web.FileResponse('/app/static/index.html') if os.path.exists('/app/static/index.html') else web.Response(text='<h1>Huddle</h1>',content_type='text/html')
def app():
    a=web.Application()
    a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',auth);a.router.add_post('/api/auth/login',auth);a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',workspaces);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_post('/api/workspaces/{slug}/channels',channels)
    a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',messages);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_get('/api/ws',ws_handler);a.router.add_get('/',index)
    return a
if __name__=='__main__':
    core.init_db();p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;web.run_app(app(),host='127.0.0.1',port=8000+NODE)
