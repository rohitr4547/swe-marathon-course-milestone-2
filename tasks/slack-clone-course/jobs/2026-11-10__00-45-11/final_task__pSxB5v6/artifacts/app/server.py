import asyncio,json,argparse,re
from aiohttp import web,WSMsgType
import core
NODE=0
def resp(x,status=200): return web.json_response(x,status=status)
def err(s,m): return resp({'error':m},s)
async def body(r):
 try:return await r.json()
 except:return None
def user(r):
 a=r.headers.get('Authorization','')
 return core.user_for_token(a[7:] if a.startswith('Bearer ') else '')
def auth(r):
 u=user(r); return u
async def health(r): return resp({'status':'ok','node_id':NODE})
async def register(r):
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid')
 with core.tx() as c:
  try:
   x=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
  except: return err(409,'duplicate')
  u=c.execute('SELECT * FROM users WHERE id=?',(x.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
 return resp({'user':core.user_to_json(u),'token':t},201)
async def login(r):
 b=await body(r); c=core.connect()
 u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if isinstance(b,dict) else '',)).fetchone(); c.close()
 if not u or not core.verify_password(b.get('password',''),u['password']):return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return resp({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=auth(r); return resp({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspaces(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); a=[core.workspace_to_json(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))];c.close();return resp({'workspaces':a})
async def create_ws(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 b=await body(r)
 if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return err(400,'invalid')
 with core.tx() as c:
  try:
   w=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',''),u['id'])).lastrowid
   g=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(w,'general')).lastrowid
   core.add_workspace_member(c,w,u['id'],'owner'); c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g,u['id']))
  except: return err(409,'duplicate')
  wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(g,)).fetchone()
 return resp({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(ch)},201)
async def ws_detail(r):
 u=auth(r); w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 c=core.connect(); cs=[core.channel_to_json(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],))];c.close()
 return resp({'workspace':core.workspace_to_json(w),'channels':cs,'read_state':{}})
async def create_channel(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('name'),str):return err(400,'invalid')
 with core.tx() as c:
  try:i=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''))).lastrowid
  except:return err(409,'duplicate')
  c.execute('INSERT INTO channel_members SELECT ?,user_id FROM members WHERE workspace_id=? AND user_id=?',(i,w['id'],u['id']))
  ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
 return resp({'channel':core.channel_to_json(ch)},201)
async def join(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 with core.tx() as c:
  if ch['workspace_id']: core.add_workspace_member(c,ch['workspace_id'],u['id'])
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
 return resp({'channel':core.channel_to_json(ch)})
async def messages(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if r.method=='POST':
  b=await body(r)
  if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty')
  return resp({'message':core.create_message(u,ch['id'],b['body'],b.get('parent_id'))},201)
 c=core.connect(); a=[core.message_to_json(x) for x in c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC',(ch['id'],))];c.close();return resp({'messages':a,'next_cursor':None})
async def ws(r):
 t=r.query.get('token');u=core.user_for_token(t)
 if not u:return err(401,'unauthorized')
 s=web.WebSocketResponse();await s.prepare(r); subs={}
 async def deliver():
  while not s.closed:
   for ch,last in list(subs.items()):
    for e in core.events_after(ch,last): await s.send_json(e);subs[ch]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(deliver())
 try:
  async for m in s:
   if m.type==WSMsgType.TEXT:
    b=json.loads(m.data);ch=int(b.get('channel_id',0))
    if b['type']=='subscribe':subs[ch]=core.head_event_id(ch);await s.send_json({'type':'subscribed','channel_id':ch,'head_event_id':subs[ch]})
    elif b['type']=='resume':
     since=int(b.get('since_event_id',0))
     replay=core.events_after(ch,since)
     for e in replay: await s.send_json(e)
     subs[ch]=replay[-1]['event_id'] if replay else since
     await s.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
 finally: task.cancel()
 return s

async def members(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(r.match_info['id'])
 if not ch:return err(404,'not found')
 c=core.connect()
 a=[core.user_to_json(x) for x in c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],))]
 c.close(); return resp({'members':a})
async def leave(r):
 u=auth(r); ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 with core.tx() as c:c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
 return resp({'channel':core.channel_to_json(ch)})
async def patch_channel(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('topic',''),str) or len(b.get('topic',''))>250:return err(400,'invalid')
 with core.tx() as c:
  c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',ch['topic']),ch['id']))
  x=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
 return resp({'channel':core.channel_to_json(x)})
async def archive(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE channels SET is_archived=? WHERE id=?',(0 if r.path.endswith('unarchive') else 1,ch['id']))
  x=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
 return resp({'channel':core.channel_to_json(x)})
async def workspace_members(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 c=core.connect(); a=[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))];c.close()
 return resp({'members':a})
async def replies(r):
 u=auth(r);parent=r.match_info['id']
 if not u:return err(401,'unauthorized')
 c=core.connect(); a=[core.message_to_json(x) for x in c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(parent,))];c.close()
 return resp({'replies':a,'next_cursor':None})

app=web.Application()
app.router.add_get('/api/health',health);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_ws);app.router.add_get('/api/workspaces/{slug}',ws_detail);app.router.add_get('/api/workspaces/{slug}/members',workspace_members);app.router.add_post('/api/workspaces/{slug}/channels',create_channel)
app.router.add_post('/api/channels/{id}/join',join);app.router.add_post('/api/channels/{id}/leave',leave);app.router.add_get('/api/channels/{id}/members',members);app.router.add_patch('/api/channels/{id}',patch_channel);app.router.add_post('/api/channels/{id}/archive',archive);app.router.add_post('/api/channels/{id}/unarchive',archive);app.router.add_get('/api/channels/{id}/messages',messages);app.router.add_post('/api/channels/{id}/messages',messages);app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_get('/api/ws',ws)
async def root(r): return web.FileResponse('/app/static/index.html')
async def appjs(r): return web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'})
app.router.add_get('/',root);app.router.add_get('/app.js',appjs)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(app,host='127.0.0.1',port=8000+NODE)
