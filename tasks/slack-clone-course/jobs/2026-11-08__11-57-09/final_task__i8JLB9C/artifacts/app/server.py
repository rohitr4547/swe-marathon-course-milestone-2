import asyncio,json,argparse,re
from aiohttp import web,WSMsgType
import core
core.init_db()
NODE=0
async def body(r):
 try: return await r.json()
 except: return None
def out(x,status=200): return web.json_response(x,status=status)
def err(s,m): return out({'error':m},s)
def auth(r):
 h=r.headers.get("Authorization","")
 if not h.startswith("Bearer "):
  q=r.query.get("token","")
  h=("Bearer "+q) if q else ""
 if not h.startswith("Bearer "):
  return None
 c=core.connect()
 x=c.execute("SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?",(h[7:],)).fetchone()
 c.close()
 return x

async def health(r): return out({'status':'ok','node_id':NODE})
async def register(r):
 d=await body(r)
 if not isinstance(d,dict) or not re.fullmatch(r'[A-Za-z0-9_]+',str(d.get('username',''))) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hp(d['password']),d.get('display_name') or d['username'])); u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); t=core.token(c,u['id'])
  return out({'user':core.userj(u),'token':t},201)
 except: return err(409,'duplicate')
async def login(r):
 d=await body(r); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username',''),)).fetchone() if isinstance(d,dict) else None
 if not u or not core.verify(str(d.get('password','')),u['password']): c.close(); return err(401,'invalid credentials')
 t=core.token(c,u['id']); c.close(); return out({'user':core.userj(u),'token':t})
async def me(r):
 u=auth(r); return out({'user':core.userj(u)}) if u else err(401,'unauthorized')
async def create_ws(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 d=await body(r)
 if not isinstance(d,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))):return err(400,'invalid')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d.get('name',d['slug']),u['id'])); wid=cur.lastrowid
   c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=cur.lastrowid; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  return out({'workspace':dict(w),'general_channel':core.chj(ch)},201)
 except: return err(409,'duplicate')
async def workspaces(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); a=[dict(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))]; c.close(); return out({'workspaces':a})
async def wsdetail(r):
 u=auth(r); c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 a=[core.chj(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],))]; c.close(); return out({'workspace':dict(w),'channels':a})
async def channels(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not w:return err(404,'not found')
 d=await body(r)
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic',''))); cid=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  return out({'channel':core.chj(ch)},201)
 except: return err(409,'duplicate')
async def join(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 cid=int(r.match_info['id'])
 with core.tx() as c:
  ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  if not ch:return err(404,'not found')
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id']))
 return out({'channel':core.chj(ch)})
async def post(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 d=await body(r); cid=int(r.match_info['id'])
 if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'empty')
 with core.tx() as c:
  ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  if not ch:return err(404,'not found')
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id'],)); cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],d['body'],d.get('parent_id'),core.now_iso())); m=c.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone(); x=core.msgj(m); core.emit(c,cid,'message.reply' if m['parent_id'] else 'message.created',{'message':x})
 return out({'message':x},201)
async def messages(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); a=[core.msgj(x) for x in c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(int(r.match_info['id']),))]; c.close(); return out({'messages':a[:200],'next_cursor':None})
async def ws(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 q=web.WebSocketResponse(); await q.prepare(r); subs={}; 
 async def loop():
  while not q.closed:
   for cid,n in list(subs.items()):
    for e in core.events_after(cid,n): await q.send_json(e); subs[cid]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(loop())
 try:
  async for m in q:
   if m.type==WSMsgType.TEXT:
    d=json.loads(m.data); cid=int(d.get('channel_id',0))
    if d['type']=='subscribe': subs[cid]=core.head(cid); await q.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
    elif d['type']=='resume': subs[cid]=int(d.get('since_event_id',0)); await asyncio.sleep(.06); await q.send_json({'type':'resumed','channel_id':cid,'head_event_id':core.head(cid)})
 finally: task.cancel()
 return q
async def root(r): return web.Response(text='<h1>Huddle</h1>',content_type='text/html')
def app():
 a=web.Application(); a.add_routes([web.get('/api/health',health),web.post('/api/auth/register',register),web.post('/api/auth/login',login),web.get('/api/auth/me',me),web.post('/api/workspaces',create_ws),web.get('/api/workspaces',workspaces),web.get('/api/workspaces/{slug}',wsdetail),web.post('/api/workspaces/{slug}/channels',channels),web.post('/api/channels/{id}/join',join),web.post('/api/channels/{id}/messages',post),web.get('/api/channels/{id}/messages',messages),web.get('/api/ws',ws),web.get('/',root)]); return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node; web.run_app(app(),host='127.0.0.1',port=8000+NODE)
