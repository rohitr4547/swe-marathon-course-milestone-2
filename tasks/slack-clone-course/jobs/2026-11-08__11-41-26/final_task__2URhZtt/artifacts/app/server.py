import asyncio,json,argparse,re
from aiohttp import web,WSMsgType
import core
def err(s,m): return web.json_response({'error':m},status=s)
async def body(r):
 try:return await r.json()
 except:return None
def tok(r): return ((r.headers.get('Authorization') or '').removeprefix('Bearer ').strip() or r.query.get('token','').removeprefix('Bearer ').strip())
def auth(r): return core.user_for_token(tok(r))
async def api(r):
 p=r.path
 if p=='/api/health': return web.json_response({'status':'ok','node_id':r.app['node']})
 u=auth(r) if p.startswith('/api/') else None
 if p.startswith('/api/') and p not in ['/api/auth/register','/api/auth/login'] and not u:return err(401,'unauthorized')
 if p=='/api/auth/register' and r.method=='POST':
  d=await body(r)
  if not isinstance(d,dict) or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid')
  try:
   with core.tx() as c:
    x=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username'],core.now_iso()));uid=x.lastrowid;t=core.issue_token(c,uid);row=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
   return web.json_response({'user':core.uj(row),'token':t},status=201)
  except: return err(409,'duplicate')
 if p=='/api/auth/login' and r.method=='POST':
  d=await body(r);c=core.connect();x=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if isinstance(d,dict) else '',)).fetchone();c.close()
  if not x or not core.verify_password(d.get('password',''),x['password']):return err(401,'invalid')
  with core.tx() as c:t=core.issue_token(c,x['id'])
  return web.json_response({'user':core.uj(x),'token':t})
 if p=='/api/auth/me':return web.json_response({'user':core.uj(u)})
 if p=='/api/workspaces' and r.method=='POST':
  d=await body(r)
  with core.tx() as c:
   try:
    x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']));wid=x.lastrowid;c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));q=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=q.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   except:return err(409,'duplicate')
  return web.json_response({'workspace':{'id':w['id'],'slug':w['slug'],'name':w['name'],'owner_id':w['owner_id'],'join_mode':'open'},'general_channel':core.cjson(ch)},status=201)
 m=re.match(r'/api/workspaces/([^/]+)/channels$',p)
 if m and r.method=='POST':
  d=await body(r);c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone();c.close()
  if not w:return err(404,'not found')
  with core.tx() as c:
   try:x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic','')));cid=x.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   except:return err(409,'duplicate')
  return web.json_response({'channel':core.cjson(ch)},status=201)
 m=re.match(r'/api/channels/(\d+)/join$',p)
 if m and r.method=='POST':
  cid=int(m.group(1)); c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close()
  if not ch:return err(404,'not found')
  with core.tx() as c:
   if ch['workspace_id']: c.execute('INSERT OR IGNORE INTO members(workspace_id,user_id,role) VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
   c.execute('INSERT OR IGNORE INTO channel_members(channel_id,user_id) VALUES(?,?)',(cid,u['id']))
   ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  return web.json_response({'channel':core.cjson(ch)})
 m=re.match(r'/api/channels/(\d+)/messages$',p)
 if m and r.method=='POST':
  d=await body(r);ch=int(m.group(1))
  if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body']:return err(400,'body')
  with core.tx() as c:r0,f=core.init_message(c,u['id'],ch,d['body'],d.get('parent_id'))
  return web.json_response({'message':core.mjson(r0)},status=201)
 m=re.match(r'/api/channels/(\d+)/messages$',p)
 if m and r.method=='GET':
  c=core.connect();rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC',(int(m.group(1)),)).fetchall();c.close();return web.json_response({'messages':[core.mjson(x) for x in rs],'next_cursor':None})
 return err(404,'not found')
async def ws(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=web.WebSocketResponse();await w.prepare(r); subs={}; last={}
 async def poll():
  while not w.closed:
   for ch,n in list(last.items()):
    for f in core.events_after(ch,n):await w.send_json(f);last[ch]=f['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for x in w:
   if x.type==WSMsgType.TEXT:
    d=json.loads(x.data);ch=int(d.get('channel_id',0))
    if d.get('type')=='subscribe': last[ch]=core.head(ch);await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':last[ch]})
    elif d.get('type')=='resume':
     last[ch]=int(d.get('since_event_id',0));await asyncio.sleep(.1);await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head(ch)})
 finally:task.cancel()
 return w
def main():
 a=argparse.ArgumentParser();a.add_argument('--node',type=int,default=0);n=a.parse_args().node;app=web.Application();app['node']=n
 app.router.add_get('/api/ws',ws);app.router.add_route('*','/{tail:.*}',api);web.run_app(app,host='127.0.0.1',port=8000+n)
if __name__=='__main__':main()
