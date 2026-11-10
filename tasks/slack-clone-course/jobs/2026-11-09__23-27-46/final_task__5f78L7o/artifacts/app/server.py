import asyncio,json,sys,base64,os
from aiohttp import web,WSMsgType
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
def js(x,status=200): return web.json_response(x,status=status)
def err(msg,status): return js({'error':msg},status=status)
async def body(r):
 try:return await r.json()
 except:return None
def auth(r):
 h=r.headers.get('Authorization','')
 if h.startswith('Bearer '): return core.token_user(h[7:])
 return core.token_user(r.query.get('token','')) if r.query.get('token') else None
def uid(r):
 u=auth(r)
 return u['id'] if u else None
def valid(s): return isinstance(s,str) and s.strip()!=''
def cur(kind,v):
 return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':int(v)}).encode()).decode().rstrip('=')
def dec(s,kind):
 try:
  x=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4))); assert x['k']==kind
  return int(x['v'])
 except: raise ValueError()
async def health(r): return js({'status':'ok','node_id':NODE})
async def register(r):
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not isinstance(b.get('password'),str) or not __import__('re').fullmatch(r'[A-Za-z0-9_]+',b['username']) or len(b['password'])<8:return err('invalid',400)
 try:u,t=core.register(b['username'],b['password'],b.get('display_name'))
 except: return err('duplicate',409)
 return js({'user':core.user_json(u),'token':t},status=201)
async def login(r):
 b=await body(r); x=core.login(b.get('username'),b.get('password')) if isinstance(b,dict) else None
 return js({'user':core.user_json(x[0]),'token':x[1]}) if x else err('invalid credentials',401)
async def me(r):
 u=auth(r); return js({'user':core.user_json(u)}) if u else err('unauthorized',401)
async def workspaces(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return js({'workspaces':[dict(x) for x in rows]})
async def create_ws(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('slug'),str) or not isinstance(b.get('name'),str):return err('invalid',400)
 try:
  with core.tx() as c:
   wid=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])).lastrowid
   c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); cid=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')).lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
 except:return err('duplicate',409)
 c=core.conn(); out=js({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},status=201); c.close(); return out
async def ws_detail(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not w:return err('not found',404)
 ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
 return js({'workspace':dict(w),'channels':[dict(x) for x in ch],'read_state':{}})
async def members(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not w:return err('not found',404)
 x=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return js({'members':[dict(z) for z in x]})
async def create_ch(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 b=await body(r);c=core.conn();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not w:return err('not found',404)
 try:
  with core.tx() as c:
   cid=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''))).lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
 except:return err('duplicate',409)
 c=core.conn(); out=js({'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},status=201); c.close(); return out
async def patch_channel(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('topic',''),str) or len(b.get('topic',''))>250:return err('invalid',400)
 c=core.conn();ch=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
 if not ch:return err('not found',404)
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():return err('forbidden',403)
 c.close()
 with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),ch['id']))
 c=core.conn();out=dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone());c.close();return js({'channel':out})
async def join(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn();ch=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
 if not ch:return err('not found',404)
 with core.tx() as c:
  if ch['workspace_id'] and not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone():c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
 return js({'channel':dict(ch)})
async def messages(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 cid=int(r.match_info['id']);c=core.conn();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
 if not ch:return err('not found',404)
 rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC LIMIT 200',(cid,)).fetchall();c.close();return js({'messages':[core.msg_json(core.conn(),x) for x in rows],'next_cursor':None})
async def replies(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn(); p=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone()
 if not p:return err('not found',404)
 rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(p['id'],)).fetchall()
 out=[core.msg_json(c,x) for x in rows];c.close();return js({'replies':out,'next_cursor':None})
async def post(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 b=await body(r)
 if not isinstance(b,dict) or not valid(b.get('body')):return err('invalid',400)
 m,e=core.make_message(u['id'],int(r.match_info['id']),b['body'],b.get('parent_id'))
 if isinstance(e,str):return err(e,int(e))
 return js({'message':m},status=201)
async def edit(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not m:return err('not found',404)
 b=await body(r)
 if not isinstance(b,dict) or not valid(b.get('body')):return err('invalid',400)
 if m['deleted']:return err('deleted',409)
 if m['author_id']!=u['id']:return err('forbidden',403)
 with core.tx() as c:
  t=core.now_iso();mentions=[]
  for name in __import__('re').findall(r'(?<!\\w)@([A-Za-z0-9_]+)',b['body']):
   z=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
   if z and z[0]!=u['id'] and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],z[0])).fetchone() and z[0] not in mentions:mentions.append(z[0])
  c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(b['body'],t,json.dumps(mentions),m['id']))
  x=c.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone();core.emit(c,m['channel_id'],'message.updated',{'message':core.msg_json(c,x)})
 c=core.conn();out=core.msg_json(c,x);c.close();return js({'message':out})
async def delete(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 c=core.conn();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone()
 if not m:return err('not found',404)
 w=c.execute('SELECT owner_id FROM workspaces w JOIN channels ch ON ch.workspace_id=w.id WHERE ch.id=?',(m['channel_id'],)).fetchone();c.close()
 if m['author_id']!=u['id'] and (not w or w[0]!=u['id']):return err('forbidden',403)
 if not m['deleted']:
  with core.tx() as c:
   c.execute('UPDATE messages SET deleted=1 WHERE id=? OR parent_id=?',(m['id'],m['id']));core.emit(c,m['channel_id'],'message.deleted',{'message_id':m['id']})
 return js({'deleted':True})
async def react(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('emoji'),str):return err('invalid',400)
 with core.tx() as c:c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(r.match_info['id'],u['id'],b['emoji']))
 c=core.conn();x=core.reactions(c,int(r.match_info['id']));c.close();return js({'reactions':x})
async def unreact(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 with core.tx() as c:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r.match_info['id'],u['id'],r.match_info['emoji']))
 c=core.conn();x=core.reactions(c,int(r.match_info['id']));c.close();return js({'reactions':x})
async def ws(r):
 u=auth(r)
 if not u:return err('unauthorized',401)
 q=web.WebSocketResponse();await q.prepare(r); subs={}
 async def poll():
  while not q.closed:
   c=core.conn()
   for cid,last in list(subs.items()):
    for x in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)):
     f=json.loads(x[0]);subs[cid]=f['event_id'];await q.send_json(f)
   c.close();await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for msg in q:
   if msg.type==WSMsgType.TEXT:
    x=json.loads(msg.data);cid=int(x.get('channel_id',0))
    if x.get('type')=='subscribe':
     c=core.conn();h=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone();c.close();subs[cid]=h[0] if h else 0;await q.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
    elif x.get('type')=='resume': subs[cid]=int(x.get('since_event_id',0));await q.send_json({'type':'resumed','channel_id':cid,'head_event_id':subs[cid]})
 finally: task.cancel()
 return q
app=web.Application()
app.router.add_get('/api/health',health);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_ws);app.router.add_get('/api/workspaces/{slug}',ws_detail);app.router.add_get('/api/workspaces/{slug}/members',members);app.router.add_post('/api/workspaces/{slug}/channels',create_ch)
app.router.add_post('/api/channels/{id}/join',join);app.router.add_patch('/api/channels/{id}',patch_channel);app.router.add_get('/api/channels/{id}/messages',messages);app.router.add_post('/api/channels/{id}/messages',post)
app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_patch('/api/messages/{id}',edit);app.router.add_delete('/api/messages/{id}',delete);app.router.add_post('/api/messages/{id}/reactions',react);app.router.add_delete('/api/messages/{id}/reactions/{emoji}',unreact);app.router.add_get('/api/ws',ws)
async def index(r):
 return web.FileResponse('/app/static/index.html')
async def appjs(r):
 return web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'})
app.router.add_get('/',index)
app.router.add_get('/app.js',appjs)
if __name__=='__main__': web.run_app(app,host='127.0.0.1',port=8000+NODE)
