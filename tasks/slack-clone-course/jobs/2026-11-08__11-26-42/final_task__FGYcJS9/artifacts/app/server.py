import asyncio,json,argparse,os,re
from aiohttp import web,WSMsgType
import core
NODE=0
async def body(req):
 try:return await req.json()
 except:return None
def out(x,status=200):return web.json_response(x,status=status)
def err(s,m):return out({'error':m},s)
async def auth(req):
 h=req.headers.get('Authorization','');return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
async def health(req):return out({'status':'ok','node_id':NODE})
async def register(req):
 d=await body(req)
 if not isinstance(d,dict) or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid registration')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username'],core.now_iso()));u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone();t=core.issue_token(c,u['id'])
   return out({'user':core.user_json(u),'token':t},201)
 except: return err(409,'duplicate username')
async def login(req):
 d=await body(req);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username'),)) .fetchone() if isinstance(d,dict) else None
 if not u or not core.verify_password(d.get('password',''),u['password']):c.close();return err(401,'invalid credentials')
 t=core.issue_token(c,u['id']);c.close();return out({'user':core.user_json(u),'token':t})
async def me(req):
 u=await auth(req);return out({'user':core.user_json(u)}) if u else err(401,'unauthorized')
async def workspaces(req):
 u=await auth(req)
 if not u:return err(401,'unauthorized')
 c=core.connect();a=[core.ws_json(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))];c.close();return out({'workspaces':a})
async def create_ws(req):
 u=await auth(req);d=await body(req)
 if not u:return err(401,'unauthorized')
 if not isinstance(d,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))) or not isinstance(d.get('name'),str):return err(400,'invalid')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']));w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(w['id'],'general'));ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']));return out({'workspace':core.ws_json(w),'general_channel':core.ch_json(ch)},201)
 except:return err(409,'duplicate')
async def ws_detail(req):
 u=await auth(req);c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(req.match_info['slug'],)).fetchone()
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 a=[core.ch_json(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],))];c.close();return out({'workspace':core.ws_json(w),'channels':a,'read_state':{}})
async def create_channel(req):
 u=await auth(req);d=await body(req);c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(req.match_info['slug'],)).fetchone()
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d.get('name'),int(bool(d.get('is_private'))),d.get('topic','')));r=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT INTO channel_members VALUES(?,?)',(r['id'],u['id']));return out({'channel':core.ch_json(r)},201)
 except:return err(409,'duplicate')
async def join(req):
 u=await auth(req);cid=int(req.match_info['id'])
 if not u:return err(401,'unauthorized')
 with core.tx() as c:
  r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  if not r:return err(404,'not found')
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id']))
 return out({'channel':core.ch_json(r)})
async def messages(req):
 u=await auth(req);cid=int(req.match_info['id'])
 if not u:return err(401,'unauthorized')
 c=core.connect();rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(cid,)).fetchall();c.close();return out({'messages':[core.msg_json(x) for x in rows],'next_cursor':None})
async def post(req):
 u=await auth(req);d=await body(req);cid=int(req.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'empty body')
 return out({'message':core.init_message(u['id'],cid,d['body'],d.get('parent_id'))},201)
async def ws_handler(req):
 u=core.user_for_token(req.query.get('token',''))
 if not u:return err(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(req);subs={};alive=True
 async def poll():
  while not ws.closed:
   for cid,last in list(subs.items()):
    for e in core.events_after(cid,last): subs[cid]=e['event_id'];await ws.send_json(e)
   await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for m in ws:
   if m.type==WSMsgType.TEXT:
    d=json.loads(m.data);cid=d.get('channel_id')
    if d.get('type')=='subscribe':subs[cid]=core.event_head(cid);await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
    elif d.get('type')=='resume':
     since=int(d.get('since_event_id',0))
     for e in core.events_after(cid,since):
      subs[cid]=e['event_id']; await ws.send_json(e)
     subs[cid]=core.event_head(cid)
     await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':subs[cid]})
 finally:task.cancel()
 return ws
def app():
 a=web.Application();a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces/{slug}',ws_detail);a.router.add_post('/api/workspaces/{slug}/channels',create_channel);a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',post);a.router.add_get('/api/ws',ws_handler);return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(app(),host='127.0.0.1',port=8000+NODE)
