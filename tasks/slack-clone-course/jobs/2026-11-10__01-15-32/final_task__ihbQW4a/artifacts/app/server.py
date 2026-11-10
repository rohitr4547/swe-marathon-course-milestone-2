import asyncio,json,argparse,re,base64,sqlite3
from aiohttp import web, WSMsgType
import core
NODE=0
def js(x, status=None, **kw): return web.json_response(x, **({'status':status} if status is not None else {}), **kw)
def err(s,n=400): return js({'error':s},status=n)
async def body(r):
 try: return await r.json()
 except: return None
def auth(r): return core.token_user((r.headers.get('Authorization') or '').removeprefix('Bearer '))
async def health(r): return js({'status':'ok','node_id':NODE})
async def register(r):
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err('invalid')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],b['password'],b.get('display_name') or b['username']))
   uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; t=__import__('secrets').token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
  return js({'user':core.user_obj(u),'token':t},201)
 except sqlite3.IntegrityError: return err('duplicate',409)
 except Exception:
  import traceback; traceback.print_exc()
  return err('internal',500)
async def login(r):
 b=await body(r); c=core.conn(); u=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username') if isinstance(b,dict) else '',b.get('password') if isinstance(b,dict) else '')).fetchone()
 if not u:c.close();return err('invalid',401)
 t=__import__('secrets').token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id']));c.close();return js({'user':core.user_obj(u),'token':t})
async def me(r):
 u=auth(r)
 return js({'user':core.user_obj(u)}) if u else err('unauthorized',401)
def member(c,ch,uid): return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,uid)).fetchone()
async def workspaces(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn(); rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return js({'workspaces':[dict(x) for x in rows]})
async def create_ws(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 b=await body(r)
 try:
  with core.tx() as c:
   c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));ch=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u['id']))
   w=dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()); ca=dict(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())
  return js({'workspace':w,'general_channel':ca},201)
 except:return err('duplicate',409)
async def workspace(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not w:return err('not found',404)
 cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
 return js({'workspace':dict(w),'channels':[dict(x) for x in cs],'read_state':{}})
async def members(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn();w=c.execute('SELECT id FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone(); rows=c.execute('SELECT user_id,role FROM memberships WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return js({'members':[dict(x) for x in rows]})
async def create_ch(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 b=await body(r);c=core.conn();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 try:
  with core.tx() as c:
   c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic','')));ch=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u['id'])); x=dict(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())
  return js({'channel':x},201)
 except:return err('duplicate',409)
async def join(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 with core.tx() as c:
  ch=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
  if not ch:return err('not found',404)
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
  if ch['workspace_id']:c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
 return js({'channel':dict(ch)})
async def messages(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC',(r.match_info['id'],)).fetchall();out=[core.msg_obj(c,x) for x in rows];c.close();return js({'messages':out,'next_cursor':None})
async def post(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 b=await body(r);ch=int(r.match_info['id'])
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err('body')
 with core.tx() as c:
  cr=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  if not cr:return err('not found',404)
  if cr['is_archived']:return err('archived',423)
  c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,u['id'],b['body'],b.get('parent_id'),core.now()));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0];r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();m=core.msg_obj(c,r);core.emit(c,ch,'message.reply' if b.get('parent_id') else 'message.created',{'message':m})
 return js({'message':m},201)
async def ws(r):
 u=auth(r) or core.token_user(r.query.get('token',''))
 if not u:return web.Response(status=401)
 w=web.WebSocketResponse();await w.prepare(r); subs={}; alive=True
 async def poll():
  while alive:
   await asyncio.sleep(.05)
   c=core.conn()
   for ch,last in list(subs.items()):
    for x in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)).fetchall():
     f=json.loads(x[0]);subs[ch]=f['event_id'];await w.send_json(f)
   c.close()
 task=asyncio.create_task(poll())
 try:
  async for x in w:
   if x.type==WSMsgType.TEXT:
    b=json.loads(x.data);ch=int(b.get('channel_id'))
    if b.get('type')=='subscribe':
     c=core.conn();head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0];c.close();subs[ch]=head;await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
    elif b.get('type')=='resume':
     since=int(b.get('since_event_id',0));c=core.conn()
     for x in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall():await w.send_json(json.loads(x[0]))
     head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0];c.close();subs[ch]=head;await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':head})
 finally: alive=False;task.cancel()
 return w
def main():
 app=web.Application();app.router.add_get('/api/health',health);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me);app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_ws);app.router.add_get('/api/workspaces/{slug}',workspace);app.router.add_get('/api/workspaces/{slug}/members',members);app.router.add_post('/api/workspaces/{slug}/channels',create_ch);app.router.add_post('/api/channels/{id}/join',join);app.router.add_get('/api/channels/{id}/messages',messages);app.router.add_post('/api/channels/{id}/messages',post);app.router.add_get('/api/ws',ws)
 web.run_app(app,host='127.0.0.1',port=8000+NODE)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();main()
