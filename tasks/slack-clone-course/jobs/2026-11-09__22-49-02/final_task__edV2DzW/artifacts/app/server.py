import asyncio,json,argparse,re
from aiohttp import web,WSMsgType
import core
def err(n,s): return web.json_response({'error':s},status=n)
async def body(r):
 try:return await r.json()
 except:return None
def user(r):
 h=r.headers.get('Authorization','')
 return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
def auth(r):
 u=user(r);return u
async def register(r):
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
 with core.tx() as c:
  try:cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
  except: return err(409,'username exists')
  u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone();t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.ujson(u),'token':t},status=201)
async def login(r):
 b=await body(r);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if isinstance(b,dict) else '',)).fetchone();c.close()
 if not u or not core.verify_password(b.get('password',''),u['password']):return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.ujson(u),'token':t})
async def me(r):
 u=auth(r)
 return web.json_response({'user':core.ujson(u)}) if u else err(401,'unauthorized')
async def health(r): return web.json_response({'status':'ok','node_id':r.app['node']})
async def workspaces(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON w.id=m.workspace_id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return web.json_response({'workspaces':[core.wjson(x) for x in rows]})
async def create_ws(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 b=await body(r)
 if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return err(400,'invalid slug')
 with core.tx() as c:
  try:
   cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',''),u['id']));wid=cur.lastrowid
   c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
  except:return err(409,'duplicate workspace')
  w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
 return web.json_response({'workspace':core.wjson(w),'general_channel':core.cjson(ch)},status=201)
async def wsdetail(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect();chs=c.execute('SELECT c.* FROM channels c JOIN channel_members m ON c.id=m.channel_id WHERE c.workspace_id=? AND m.user_id=?',(w['id'],u['id'])).fetchall();c.close()
 return web.json_response({'workspace':core.wjson(w),'channels':[core.cjson(x) for x in chs],'read_state':{}})
async def create_ch(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug']);b=await body(r)
 if not w:return err(404,'not found')
 with core.tx() as c:
  try:cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b.get('name'),int(bool(b.get('is_private'))),b.get('topic','')));cid=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
  except:return err(409,'duplicate channel')
  ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
 return web.json_response({'channel':core.cjson(ch)},status=201)
async def join(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
 return web.json_response({'channel':core.cjson(ch)})
async def get_channel_api(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 return web.json_response({'channel':core.cjson(ch)})
async def patch_channel(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return err(400,'invalid topic')
 with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']))
 return web.json_response({'channel':core.cjson(core.get_channel(ch['id']))})
async def messages(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 c=core.connect();rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(ch['id'],)).fetchall();c.close()
 return web.json_response({'messages':[core.mjson(x) for x in rows],'next_cursor':None})
async def post(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty body')
 with core.tx() as c:mid=core.addmsg(c,ch['id'],u['id'],b['body'],b.get('parent_id'));m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
 return web.json_response({'message':core.mjson(m)},status=201)
async def edit(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not m:return err(404,'not found')
 if m['author_id']!=u['id']:return err(403,'forbidden')
 if m['deleted']:return err(409,'deleted')
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'invalid body')
 with core.tx() as c:
  ch=core.get_channel(m['channel_id'])
  mentions=core.resolve_mentions(c,b['body'],ch,u['id'])
  c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(b['body'],core.now_iso(),json.dumps(mentions),m['id']))
  m=c.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone()
  core.emit(c,m['channel_id'],'message.updated',{'message':core.mjson(m,c)})
 return web.json_response({'message':core.mjson(m)})
async def delete(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not m:return err(404,'not found')
 if m['author_id']!=u['id']:
  ch=core.get_channel(m['channel_id'])
  if not ch or core.role(ch['workspace_id'],u['id'])!='owner': return err(403,'forbidden')
 with core.tx() as c:
  if not m['deleted']:c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m['id'],));core.emit(c,m['channel_id'],'message.deleted',{'message_id':m['id']})
 return web.json_response({'deleted':True})
async def reaction_api(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 mid=int(r.match_info['id']);b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('emoji'),str):return err(400,'invalid emoji')
 with core.tx() as c:c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],b['emoji']))
 c=core.connect();rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall();c.close()
 return web.json_response({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rs]})
async def replies(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(r.match_info['id'],)).fetchall();c.close()
 return web.json_response({'replies':[core.mjson(x) for x in rows],'next_cursor':None})
async def ws(r):
 u=auth(r)
 if not u:u=core.user_for_token(r.query.get('token','').removeprefix('Bearer '))
 if not u:return err(401,'unauthorized')
 x=web.WebSocketResponse();await x.prepare(r);subs={};alive=True
 async def deliver():
  while alive:
   await asyncio.sleep(.05)
   for cid,last in list(subs.items()):
    c=core.connect();rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall();c.close()
    for z in rows:
     try: await x.send_str(z['payload']);subs[cid]=z['event_id']
     except: return
 task=asyncio.create_task(deliver())
 try:
  async for q in x:
   if q.type==WSMsgType.TEXT:
    d=json.loads(q.data);cid=int(d.get('channel_id'));c=core.connect();head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()[0];c.close()
    if d['type']=='resume': subs[cid]=int(d.get('since_event_id',0))
    else: subs[cid]=head
    await x.send_json({'type':'subscribed' if d['type']=='subscribe' else 'resumed','channel_id':cid,'head_event_id':head})
 finally:
  alive=False;task.cancel();await x.close()
 return x
async def app(node):
 a=web.Application();a['node']=node
 a.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'));a.router.add_get('/app.js',lambda r:web.FileResponse('/app/static/app.js'))
 a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
 a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces/{slug}',wsdetail);a.router.add_post('/api/workspaces/{slug}/channels',create_ch)
 a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/channels/{id}',get_channel_api);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',post)
 a.router.add_post('/api/messages/{id}/reactions',reaction_api);a.router.add_patch('/api/messages/{id}',edit);a.router.add_delete('/api/messages/{id}',delete);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_get('/api/ws',ws)
 return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);n=p.parse_args().node;core.init_db();web.run_app(asyncio.run(app(n)),host='127.0.0.1',port=8000+n)
