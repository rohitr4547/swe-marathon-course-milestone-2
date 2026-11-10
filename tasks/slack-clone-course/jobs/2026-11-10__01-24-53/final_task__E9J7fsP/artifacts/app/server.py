import asyncio,json,argparse,os,re
from aiohttp import web,WSMsgType
import core
NODE=0
def json_error(n,m): return web.json_response({'error':m},status=n)
async def read_json(r):
 try:
  x=await r.json(); return x if isinstance(x,dict) else None
 except:return None
def require_user(r):
 h=r.headers.get('Authorization','')
 if not h.startswith('Bearer '): return None
 return core.user_for_token(h[7:])
def juser(r): return web.json_response({'user':core.user_to_json(r)})
async def register(req):
 d=await read_json(req)
 if not d or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or not isinstance(d.get('password'),str) or len(d['password'])<8:return json_error(400,'invalid registration')
 with core.tx() as c:
  try:
   x=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']));u=c.execute('SELECT * FROM users WHERE id=?',(x.lastrowid,)).fetchone();t=core.issue_token(c,u['id'])
  except: return json_error(409,'username exists')
 return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
async def login(req):
 d=await read_json(req);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if d else '',)).fetchone();c.close()
 if not u or not isinstance(d.get('password'),str) or not core.verify_password(d['password'],u['password']):return json_error(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.user_to_json(u),'token':t})
async def me(req):
 u=require_user(req);return juser(u) if u else json_error(401,'unauthorized')
async def health(req): return web.json_response({'status':'ok','node_id':NODE})
async def workspaces(req):
 u=require_user(req)
 if not u:return json_error(401,'unauthorized')
 c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_workspace(req):
 u=require_user(req)
 if not u:return json_error(401,'unauthorized')
 d=await read_json(req)
 if not d or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')) or not isinstance(d.get('name'),str):return json_error(400,'invalid workspace')
 with core.tx() as c:
  try:
   w=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id'])).lastrowid
   c.execute('INSERT INTO members VALUES(?,?,?)',(w,u['id'],'owner'));ch=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(w,'general')).lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u['id']))
  except:return json_error(409,'duplicate workspace')
  workspace_json=core.workspace_to_json(c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone())
  channel_json=core.channel_to_json(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())
 return web.json_response({'workspace':workspace_json,'general_channel':channel_json},status=201)
async def workspace(req):
 u=require_user(req)
 if not u:return json_error(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return json_error(404,'not found')
 c=core.connect();cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
 return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in cs],'read_state':{}})
async def members(req):
 u=require_user(req);w=core.get_workspace(req.match_info['slug']) if u else None
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 c=core.connect();x=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return web.json_response({'members':[dict(a) for a in x]})
async def create_channel(req):
 u=require_user(req);w=core.get_workspace(req.match_info['slug']) if u else None
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin','member'):return json_error(403,'forbidden')
 d=await read_json(req)
 if not d or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']):return json_error(400,'invalid channel')
 with core.tx() as c:
  try:
   i=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic',''))).lastrowid
   c.execute('INSERT INTO channel_members(channel_id,user_id) VALUES(?,?)',(i,u['id']))
  except:return json_error(409,'duplicate channel')
 channel_json=core.channel_to_json(core.get_channel(i))
 return web.json_response({'channel':channel_json},status=201)
async def join(req):
 u=require_user(req);ch=core.get_channel(req.match_info['id']) if u else None
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 with core.tx() as c:
  if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
 return web.json_response({'channel':core.channel_to_json(ch)})
async def post_message(req):
 u=require_user(req);ch=core.get_channel(req.match_info['id']) if u else None
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 if ch['is_archived']:return json_error(423,'archived')
 d=await read_json(req)
 if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'empty body')
 c=core.connect();ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone();c.close()
 if not ok:return json_error(403,'not a member')
 m=core.create_message(u,ch['id'],d['body'],d.get('parent_id'));return web.json_response({'message':core.message_to_json(m)},status=201)
async def messages(req):
 u=require_user(req);ch=core.get_channel(req.match_info['id']) if u else None
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 c=core.connect();rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC LIMIT 200',(ch['id'],)).fetchall();c.close();return web.json_response({'messages':[core.message_to_json(x) for x in rs],'next_cursor':None})
async def ws_handler(req):
 t=req.query.get('token','');u=core.user_for_token(t)
 if not u:return json_error(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(req);subs={};alive=True
 async def poll():
  while alive:
   for ch,last in list(subs.items()):
    for e in core.events_after(ch,last): await ws.send_json(e);subs[ch]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for msg in ws:
   if msg.type==WSMsgType.TEXT:
    d=json.loads(msg.data);ch=int(d.get('channel_id',0))
    if d.get('type')=='subscribe':subs[ch]=core.head_event_id(ch);await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':subs[ch]})
    elif d.get('type')=='resume':
     n=int(d.get('since_event_id',0));subs[ch]=n
     for e in core.events_after(ch,n):await ws.send_json(e);subs[ch]=e['event_id']
     await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
 finally:
  alive=False;task.cancel()
 return ws
async def root(req): return web.Response(text='<html><body><h1>Huddle</h1></body></html>',content_type='text/html')
def app():
 a=web.Application();a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_post('/api/workspaces/{slug}/channels',create_channel);a.router.add_post('/api/channels/{id}/join',join);a.router.add_post('/api/channels/{id}/messages',post_message);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_get('/api/ws',ws_handler);a.router.add_get('/',root);return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node
 web.run_app(app(),host='127.0.0.1',port=8000+NODE)
