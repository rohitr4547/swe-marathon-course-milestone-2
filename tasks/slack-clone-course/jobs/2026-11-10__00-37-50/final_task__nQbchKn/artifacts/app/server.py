import argparse,json,re,asyncio,base64
from aiohttp import web,WSMsgType
import core
NODE=0
async def body(req):
 try:return await req.json()
 except:return None
def err(s,m):return web.json_response({'error':m},status=s)
def user(req):
 h=req.headers.get('Authorization','');return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
def auth(req):
 u=user(req)
 if not u: raise web.HTTPUnauthorized(text=json.dumps({'error':'unauthorized'}),content_type='application/json')
 return u
async def health(req):return web.json_response({'status':'ok','node_id':NODE})
async def register(req):
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']));u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone();t=core.issue_token(c,u['id'])
  return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
 except Exception:return err(409,'duplicate')
async def login(req):
 b=await body(req);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if isinstance(b,dict) else '',)).fetchone();c.close()
 if not u or not core.verify_password(b.get('password',''),u['password']):return err(401,'invalid')
 with core.tx() as c:return web.json_response({'user':core.user_to_json(u),'token':core.issue_token(c,u['id'])})
async def me(req):return web.json_response({'user':core.user_to_json(auth(req))})
async def workspaces(req):
 u=auth(req);c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_ws(req):
 u=auth(req);b=await body(req)
 if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return err(400,'invalid')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',''),u['id']));w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(w['id'],'general'));ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
  return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},status=201)
 except:return err(409,'duplicate')
async def ws(req):
 token=req.query.get('token','')
 if token.startswith('Bearer '): token=token[7:]
 u=core.user_for_token(token)
 if not u:return web.Response(status=401,text=json.dumps({'error':'unauthorized'}),content_type='application/json')
 w=web.WebSocketResponse();await w.prepare(req);subs={};closed=False
 async def deliver():
  nonlocal closed
  while not closed:
   try:
    c=core.connect()
    for ch,last in list(subs.items()):
     rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)).fetchall()
     for r in rows:
      x=json.loads(r['payload']);await w.send_json(x);subs[ch]=x['event_id']
    c.close();await asyncio.sleep(.05)
   except: await asyncio.sleep(.1)
 task=asyncio.create_task(deliver())
 try:
  async for msg in w:
   if msg.type!=WSMsgType.TEXT:continue
   try:b=json.loads(msg.data)
   except:continue
   ch=b.get('channel_id');typ=b.get('type')
   if typ=='subscribe' and isinstance(ch,int):
    c=core.connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close()
    if r:subs[ch]=r[0];await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':r[0]})
   elif typ=='resume' and isinstance(ch,int):
    since=int(b.get('since_event_id',0));c=core.connect();rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall();head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close()
    for r in rows:await w.send_json(json.loads(r['payload']))
    subs[ch]=head[0] if head else since
    await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':subs[ch]})
 finally:
  closed=True;task.cancel()
 return w
async def ws_create(req):
 u=auth(req);ch=core.get_channel(int(req.match_info['id']));b=await body(req)
 if not ch:return err(404,'not found')
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'invalid')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
  c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],b['body'],b.get('parent_id'),core.now_iso()))
  m=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone();mj=core.message_json(c,m)
  core.emit_event(c,ch['id'],'message.reply' if b.get('parent_id') else 'message.created',{'message':mj})
 return web.json_response({'message':mj},status=201)
async def channel_messages(req):
 u=auth(req);ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 c=core.connect();rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(ch['id'],)).fetchall();out=[core.message_json(c,r) for r in rs];c.close();return web.json_response({'messages':out,'next_cursor':None})
async def ws_detail(req):
 u=auth(req);w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect();chs=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in chs],'read_state':{}})
async def create_channel(req):
 u=auth(req);w=core.get_workspace(req.match_info['slug']);b=await body(req)
 if not w:return err(404,'not found')
 if not isinstance(b,dict) or not isinstance(b.get('name'),str):return err(400,'invalid')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic','')));ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
  return web.json_response({'channel':core.channel_to_json(ch)},status=201)
 except:return err(409,'duplicate')
async def join(req):
 u=auth(req);ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 with core.tx() as c:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
 return web.json_response({'channel':core.channel_to_json(ch)})
async def members(req):
 auth(req);ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 c=core.connect();rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close();return web.json_response({'members':[core.user_to_json(x) for x in rs]})
def make_app():
 a=web.Application();a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces/{slug}',ws_detail);a.router.add_post('/api/workspaces/{slug}/channels',create_channel);a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/channels/{id}/members',members);a.router.add_post('/api/channels/{id}/messages',ws_create);a.router.add_get('/api/channels/{id}/messages',channel_messages);a.router.add_get('/api/ws',ws);return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node
 web.run_app(make_app(),host='127.0.0.1',port=8000+NODE)
