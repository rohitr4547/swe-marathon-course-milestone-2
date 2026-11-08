import asyncio,json,argparse,os,re,uuid
from aiohttp import web,WSMsgType
import core
def json_error(s,m): return web.json_response({'error':m},status=s)
async def read_json(r):
    try:
        x=await r.json(); return x if isinstance(x,dict) else None
    except:return None
async def require_user(r):
    h=r.headers.get('Authorization',''); token=h[7:] if h.startswith('Bearer ') else None
    return core.user_for_token(token)
def ok(x,status=200): return web.json_response(x,status=status)
def check_fields(x,fields):
    return x is not None and all((k not in x or isinstance(x[k],t)) for k,t in fields.items())
async def health(r): return ok({'status':'ok','node_id':r.app['node']})
async def register(r):
    x=await read_json(r)
    if not check_fields(x,{'username':str,'password':str}) or not re.fullmatch(r'[A-Za-z0-9_]+',x['username']) or len(x['password'])<8:return json_error(400,'invalid registration')
    try:
        with core.tx() as c:
            q=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(x['username'],core.hash_password(x['password']),x.get('display_name') or x['username']))
            u=c.execute('SELECT * FROM users WHERE id=?',(q.lastrowid,)).fetchone();t=core.issue_token(c,u['id'])
        return ok({'user':core.user_to_json(u),'token':t},201)
    except: return json_error(409,'username exists')
async def login(r):
    x=await read_json(r)
    if not check_fields(x,{'username':str,'password':str}):return json_error(400,'invalid body')
    c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(x['username'],)).fetchone()
    if not u or not core.verify_password(x['password'],u['password']):c.close();return json_error(401,'invalid credentials')
    t=core.issue_token(c,u['id']);c.close();return ok({'user':core.user_to_json(u),'token':t})
async def me(r):
    u=await require_user(r);return ok({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def create_workspace(r):
    u=await require_user(r)
    if not u:return json_error(401,'unauthorized')
    x=await read_json(r)
    if not check_fields(x,{'slug':str,'name':str}) or not re.fullmatch(r'[a-z0-9-]{2,32}',x['slug']):return json_error(400,'invalid workspace')
    try:
      with core.tx() as c:
        q=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],u['id']));wid=q.lastrowid
        c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(wid,u['id'],'owner'));ch=core.create_channel_row(c,wid,'general');core.add_channel_member(c,ch['id'],u['id'])
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()
      return ok({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
    except:return json_error(409,'workspace exists')
async def workspaces(r):
    u=await require_user(r)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();a=[core.workspace_to_json(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))];c.close();return ok({'workspaces':a})
async def workspace(r):
    u=await require_user(r)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(r.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect();mem=c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
    if not mem:return json_error(403,'forbidden')
    cs=[core.channel_to_json(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],r.query.get('include_archived') in ('1','true')))]
    c.close();return ok({'workspace':core.workspace_to_json(w),'channels':cs,'read_state':{}})
async def members(r):
    u=await require_user(r);w=core.get_workspace(r.match_info['slug']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not w:return json_error(404,'not found')
    c=core.connect();a=[dict(x) for x in c.execute('SELECT user_id,role FROM workspace_members WHERE workspace_id=?',(w['id'],))];c.close();return ok({'members':a})
async def create_channel(r):
    u=await require_user(r);w=core.get_workspace(r.match_info['slug']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin','member'):return json_error(403,'forbidden')
    x=await read_json(r)
    if not check_fields(x,{'name':str}) or not re.fullmatch(r'[a-z0-9-]{1,32}',x['name']):return json_error(400,'invalid channel')
    if x.get('is_private') and core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    try:
      with core.tx() as c:
        ch=core.create_channel_row(c,w['id'],x['name'],x.get('is_private',False),x.get('topic',''));core.add_channel_member(c,ch['id'],u['id'])
      return ok({'channel':core.channel_to_json(ch)},201)
    except:return json_error(409,'channel exists')
async def channel_join(r):
    u=await require_user(r)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(r.match_info['id'])
    if not ch:return json_error(404,'not found')
    if ch['is_private'] and not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    return ok({'channel':core.channel_to_json(core.join_channel(u,ch['id']))})
async def channel_leave(r):
    u=await require_user(r);ch=core.get_channel(r.match_info['id']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    with core.tx() as c:core.remove_channel_member(c,ch['id'],u['id'])
    return ok({'channel':core.channel_to_json(ch)})
async def channel_members(r):
    u=await require_user(r);ch=core.get_channel(r.match_info['id']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    c=core.connect();a=[core.user_to_json(x) for x in c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],))];c.close();return ok({'members':a})
async def patch_channel(r):
    u=await require_user(r);ch=core.get_channel(r.match_info['id']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    x=await read_json(r)
    if not check_fields(x,{'topic':str}) or len(x.get('topic',''))>250:return json_error(400,'invalid topic')
    with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(x['topic'],ch['id']));ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return ok({'channel':core.channel_to_json(ch)})
async def archive(r):
    u=await require_user(r);ch=core.get_channel(r.match_info['id']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    w=core.connect().execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if not w or w['owner_id']!=u['id']:return json_error(403,'forbidden')
    val=1 if r.path.endswith('archive') else 0
    with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']));ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return ok({'channel':core.channel_to_json(ch)})
async def post_message(r):
    u=await require_user(r);ch=core.get_channel(r.match_info['id']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    x=await read_json(r)
    if not check_fields(x,{'body':str}) or not x['body'].strip():return json_error(400,'empty message')
    if ch['is_archived']:return json_error(423,'archived')
    try:return ok({'message':core.create_message(u,ch['id'],x['body'],x.get('parent_id'))},201)
    except PermissionError:return json_error(403,'forbidden')
    except:return json_error(400,'invalid message')
async def get_messages(r):
    u=await require_user(r);ch=core.get_channel(r.match_info['id']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    try:n,c=core.parse_paging(r.query,'messages')
    except:return json_error(400,'invalid cursor')
    a,nx=core.list_messages(ch['id'],n,c);return ok({'messages':a,'next_cursor':nx})
async def replies(r):
    u=await require_user(r)
    if not u:return json_error(401,'unauthorized')
    try:n,c=core.parse_paging(r.query,'replies')
    except:return json_error(400,'invalid cursor')
    a,nx=core.list_replies(int(r.match_info['id']),n,c);return ok({'replies':a,'next_cursor':nx})
async def ws_handler(r):
    u=await require_user(type('R',(),{'headers':{'Authorization':'Bearer '+r.query.get('token','')}})())
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse();await ws.prepare(r);subs={};alive=True
    async def deliver():
      while alive:
        for ch,last in list(subs.items()):
          for e in core.events_after(ch,last):
            await ws.send_json(e);subs[ch]=e['event_id']
        await asyncio.sleep(.05)
    task=asyncio.create_task(deliver())
    try:
      async for msg in ws:
        if msg.type==WSMsgType.TEXT:
          x=json.loads(msg.data);ch=int(x.get('channel_id',0))
          if x.get('type')=='subscribe':
            head=core.head_event_id(ch); subs[ch]=head; await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
          elif x.get('type')=='resume':
            since=int(x.get('since_event_id',0));subs[ch]=since
            for e in core.events_after(ch,since):await ws.send_json(e);subs[ch]=e['event_id']
            await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
    finally:alive=False;task.cancel()
    return ws
async def root(r):
    try:return web.FileResponse('/app/static/index.html')
    except:return web.Response(text='Huddle')
def app(node):
    a=web.Application();a['node']=node
    a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces',workspaces);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/channels/{id}/join',channel_join);a.router.add_post('/api/channels/{id}/leave',channel_leave);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_post('/api/channels/{id}/archive',archive);a.router.add_post('/api/channels/{id}/unarchive',archive)
    a.router.add_post('/api/channels/{id}/messages',post_message);a.router.add_get('/api/channels/{id}/messages',get_messages);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_get('/api/ws',ws_handler);a.router.add_get('/',root);return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);n=p.parse_args().node;core.init_db();web.run_app(app(n),host='127.0.0.1',port=8000+n)
