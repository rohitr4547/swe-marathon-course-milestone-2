import sys,json,asyncio, re
from aiohttp import web,WSMsgType
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
def json_error(s,m): return web.json_response({'error':m},status=s)
async def read_json(r):
 try:
  x=await r.json(); return x if isinstance(x,dict) else None
 except: return None
async def require_user(r):
 h=r.headers.get('Authorization','')
 return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
def auth(u): return json_error(401,'unauthorized') if not u else None
def paging(q,kind):
 try:
  lim=int(q.get('limit','50'))
  if lim<1 or lim>200: raise ValueError()
  cur=q.get('cursor'); after=0
  if cur:
   x=json.loads(__import__('base64').urlsafe_b64decode(cur+'===')); 
   if x.get('k')!=kind: raise ValueError()
   after=int(x['v'])
  return lim,after
 except: raise ValueError()
def cursor(k,v):
 return __import__('base64').urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
async def health(r): return web.json_response({'status':'ok','node_id':NODE})
async def register(r):
 b=await read_json(r)
 if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid registration')
 with core.tx() as c:
  try:
   x=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
  except: return json_error(409,'duplicate username')
  u=c.execute('SELECT * FROM users WHERE id=?',(x.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
async def login(r):
 b=await read_json(r); c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if b else '',)).fetchone();c.close()
 if not u or not verify_safe(b.get('password') if b else '',u['password']):return json_error(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.user_to_json(u),'token':t})
def verify_safe(a,b):
 try:return core.verify_password(a,b)
 except:return False
async def me(r):
 u=await require_user(r)
 return auth(u) or web.json_response({'user':core.user_to_json(u)})
async def workspaces(r):
 u=await require_user(r)
 if not u:return auth(u)
 c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(r):
 u=await require_user(r)
 if not u:return auth(u)
 b=await read_json(r)
 if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str):return json_error(400,'invalid workspace')
 with core.tx() as c:
  try:x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']))
  except:return json_error(409,'duplicate slug')
  wid=x.lastrowid;gcur=c.execute("INSERT INTO channels(workspace_id,name) VALUES(?, 'general')",(wid,));gid=gcur.lastrowid;add=c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channel_members VALUES(?,?)',(gid,u['id']))
  w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();g=c.execute('SELECT * FROM channels WHERE id=?',(gid,)).fetchone()
 return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(g)},status=201)
async def workspace(r):
 u=await require_user(r)
 if not u:return auth(u)
 w=core.get_workspace(r.match_info['slug'])
 if not w:return json_error(404,'not found')
 c=core.connect();rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0)',(w['id'],)).fetchall();c.close()
 return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':{}})
async def channels_create(r):
 u=await require_user(r)
 if not u:return auth(u)
 w=core.get_workspace(r.match_info['slug']);b=await read_json(r)
 if not w:return json_error(404,'not found')
 if core.member_role(w['id'],u['id']) is None:return json_error(403,'forbidden')
 if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return json_error(400,'invalid name')
 with core.tx() as c:
  try:cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic','')));x=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(x,u['id']))
  except:return json_error(409,'duplicate channel')
  row=c.execute('SELECT * FROM channels WHERE id=?',(x,)).fetchone()
 return web.json_response({'channel':core.channel_to_json(row)},status=201)
async def join(r):
 u=await require_user(r)
 if not u:return auth(u)
 ch=core.get_channel(r.match_info['id'])
 if not ch:return json_error(404,'not found')
 with core.tx() as c:
  if ch['workspace_id']: core.add_workspace_member(c,ch['workspace_id'],u['id'])
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
 return web.json_response({'channel':core.channel_to_json(ch)})
async def channel_patch(r):
 u=await require_user(r)
 if not u:return auth(u)
 ch=core.get_channel(r.match_info['id'])
 if not ch:return json_error(404,'not found')
 b=await read_json(r)
 if not b or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return json_error(400,'invalid topic')
 if ch['workspace_id'] and core.member_role(ch['workspace_id'],u['id']) is None:return json_error(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']))
  row=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
 return web.json_response({'channel':core.channel_to_json(row)})
async def messages(r):
 u=await require_user(r)
 if not u:return auth(u)
 ch=core.get_channel(r.match_info['id'])
 if not ch:return json_error(404,'not found')
 if r.method=='POST':
  b=await read_json(r)
  if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty body')
  try:return web.json_response({'message':core.create_message(u,ch['id'],b['body'],b.get('parent_id'))},status=201)
  except PermissionError:return json_error(403,'forbidden')
 c=core.connect();rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(ch['id'],)).fetchall();c.close()
 return web.json_response({'messages':[core.message_to_json(x) for x in rows],'next_cursor':None})
async def edit(r):
 u=await require_user(r)
 if not u:return auth(u)
 b=await read_json(r); mid=r.match_info['id'];c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return json_error(404,'not found')
 if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'invalid body')
 if m['deleted']:return json_error(409,'deleted')
 if m['author_id']!=u['id']:return json_error(403,'forbidden')
 with core.tx() as c:
  edited=core.now_iso(); mentions=core.resolve_mentions(c,b['body'],c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone(),u['id']); c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(b['body'],edited,json.dumps(mentions),mid)); x=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); payload=dict(x); payload['author']=core.user_to_json(c.execute('SELECT * FROM users WHERE id=?',(x['author_id'],)).fetchone()); payload['parent_id']=x['parent_id']; payload['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(x['id'],)).fetchone()[0]; payload['mentions']=mentions; payload['reactions']=[]; core.emit_event(c,m['channel_id'],'message.updated',{'message':payload})
 return web.json_response({'message':core.message_to_json(x)})
async def delete(r):
 u=await require_user(r)
 if not u:return auth(u)
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not m:return json_error(404,'not found')
 role=core.member_role(core.get_channel(m['channel_id'])['workspace_id'],u['id']) if core.get_channel(m['channel_id'])['workspace_id'] else None
 if m['author_id']!=u['id'] and role!='owner':return json_error(403,'forbidden')
 if m['deleted']: return web.json_response({'deleted':True})
 with core.tx() as c:
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m['id'],)); core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':m['id']})
 return web.json_response({'deleted':True})
async def reaction(r):
 u=await require_user(r)
 if not u:return auth(u)
 mid=int(r.match_info['id']); b=await read_json(r) if r.method=='POST' else {}
 c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
 if not m:return json_error(404,'not found')
 emoji=b.get('emoji') if r.method=='POST' else r.match_info.get('emoji')
 if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
 with core.tx() as c:
  if r.method=='POST': c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
  else:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
 return web.json_response({'reactions':core.reactions_for(mid)})
async def replies(r):
 u=await require_user(r)
 if not u:return auth(u)
 c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(r.match_info['id'],)).fetchall();c.close()
 return web.json_response({'replies':[core.message_to_json(x) for x in rows],'next_cursor':None})
async def ws(r):
 u=await require_user(r)
 if not u: u=core.user_for_token(r.query.get('token'))
 if not u:return auth(u)
 w=web.WebSocketResponse(); await w.prepare(r)
 subs={}; stop=False
 async def sender():
  while not stop:
   try:
    for ch,last in list(subs.items()):
     for ev in core.events_after(ch,last):
      await w.send_str(ev['payload']); subs[ch]=ev['event_id']
    await asyncio.sleep(.05)
   except Exception: break
 task=asyncio.create_task(sender())
 try:
  async for msg in w:
   if msg.type==WSMsgType.TEXT:
    try: data=json.loads(msg.data)
    except: continue
    typ=data.get('type'); ch=data.get('channel_id')
    if typ in ('subscribe','resume') and isinstance(ch,int):
     row=core.get_channel(ch)
     if not row: continue
     since=int(data.get('since_event_id',0)) if typ=='resume' else core.head_event_id(ch)
     if typ=='subscribe': subs[ch]=since
     else:
      subs[ch]=since
      for ev in core.events_after(ch,since):
       await w.send_str(ev['payload']); subs[ch]=ev['event_id']
     await w.send_json({'type':'subscribed' if typ=='subscribe' else 'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
 finally:
  stop=True; task.cancel()
 return w
app=web.Application(); app.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'))
app.router.add_get('/api/health',health);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_workspace);app.router.add_get('/api/workspaces/{slug}',workspace);app.router.add_post('/api/workspaces/{slug}/channels',channels_create)
app.router.add_post('/api/channels/{id}/join',join);app.router.add_patch('/api/channels/{id}',channel_patch);app.router.add_route('*','/api/channels/{id}/messages',messages);app.router.add_patch('/api/messages/{id}',edit);app.router.add_post('/api/messages/{id}/reactions',reaction);app.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction);app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_delete('/api/messages/{id}',delete);app.router.add_get('/api/ws',ws)
core.init_db();web.run_app(app,host='127.0.0.1',port=8000+NODE)
