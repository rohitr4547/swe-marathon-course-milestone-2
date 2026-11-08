import argparse,os,json,re,base64,asyncio,uuid
from aiohttp import web,WSMsgType
import core
def err(s,m): return web.json_response({'error':m},status=s)
async def body(r):
 try:
  x=await r.json();return x if isinstance(x,dict) else None
 except:return None
def auth(r):
 h=r.headers.get('Authorization','')
 t=h[7:] if h.startswith('Bearer ') else r.query.get('token')
 return core.user_for_token(t)
def need(r):
 u=auth(r);return u
def page(q,kind):
 try:
  n=int(q.get('limit','50')); assert 1<=n<=200
  cur=q.get('cursor'); v=0
  if cur:
   v=int(json.loads(base64.urlsafe_b64decode(cur+'==='))['v'])
  return n,v
 except: raise ValueError()
def cur(kind,v): return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':v}).encode()).decode().rstrip('=')
async def register(r):
 x=await body(r)
 if not x or not isinstance(x.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',x['username']) or not isinstance(x.get('password'),str) or len(x['password'])<8:return err(400,'invalid registration')
 try:
  with core.tx() as c:
   q=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(x['username'],core.hash_password(x['password']),x.get('display_name') or x['username']));u=c.execute('SELECT * FROM users WHERE id=?',(q.lastrowid,)).fetchone();t=core.issue_token(c,u['id'])
  return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
 except: return err(409,'username exists')
async def login(r):
 x=await body(r);u=core.user_for_token(None)
 c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(x.get('username') if x else '',)).fetchone();c.close()
 if not u or not x or not core.verify_password(x.get('password',''),u['password']):return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=need(r);return web.json_response({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def health(r):return web.json_response({'status':'ok','node_id':r.app['node']})
async def workspaces(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return web.json_response({'workspaces':[core.workspace_to_json(z) for z in x]})
async def create_ws(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 x=await body(r)
 if not x or not re.fullmatch(r'[a-z0-9-]{2,32}',str(x.get('slug',''))):return err(400,'invalid slug')
 try:
  with core.tx() as c:
   q=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x.get('name',''),u['id']));w=q.lastrowid;c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(u['id'],w,'owner'));q=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(w,'general'));ch=q.lastrowid;core.add_channel_member(c,u['id'],ch);wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone();cr=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  return web.json_response({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(cr)},status=201)
 except:return err(409,'duplicate')
async def ws_detail(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();chs=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(z) for z in chs],'read_state':{}})
async def members(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect();x=c.execute('SELECT m.user_id,m.role,u.username,u.display_name FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close();return web.json_response({'members':[dict(z) for z in x]})
async def create_ch(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 x=await body(r)
 if not x or not re.fullmatch(r'[a-z0-9-]{1,32}',str(x.get('name',''))):return err(400,'invalid name')
 try:
  with core.tx() as c:q=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')));core.add_channel_member(c,u['id'],q.lastrowid);ch=c.execute('SELECT * FROM channels WHERE id=?',(q.lastrowid,)).fetchone()
  return web.json_response({'channel':core.channel_to_json(ch)},status=201)
 except:return err(409,'duplicate')
async def join(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):
  w=core.connect().execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
  if w['join_mode']!='open':return err(403,'forbidden')
  with core.tx() as c:core.add_workspace_member(c,w['id'],u['id'])
 with core.tx() as c:core.add_channel_member(c,u['id'],ch['id'])
 return web.json_response({'channel':core.channel_to_json(ch)})
async def post_msg(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 c=core.connect();ok=c.execute('SELECT 1 FROM memberships WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone();c.close()
 if not ok:return err(403,'forbidden')
 if ch['is_archived'] and not ((await body(r) or {}).get('body')=='/unarchive'):return err(423,'archived')
 x=await body(r);b=x.get('body') if x else None
 if not isinstance(b,str) or not b.strip():return err(400,'empty body')
 fids=x.get('file_ids',[]) if x else []
 if not isinstance(fids,list):return err(400,'file_ids')
 c=core.connect()
 for fid in fids:
  z=c.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone()
  if not z:c.close();return err(400,'invalid file')
 c.close()
 if b.startswith('//'): b=b[1:]
 elif b.strip() == '/me': return err(400,'missing text')
 elif b.startswith('/topic '):
  text=b[7:].strip()
  if not text:return err(400,'topic')
  with core.tx() as c:
   c.execute('UPDATE channels SET topic=? WHERE id=?',(text,ch['id']));z=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
  return web.json_response({'message':None,'channel':core.channel_to_json(z)},status=201)
 elif b.startswith('/invite '):
  target=b[8:].strip().lstrip('@');c=core.connect();z=c.execute('SELECT * FROM users WHERE username=?',(target,)).fetchone();c.close()
  if not z:return err(400,'user')
  with core.tx() as c:
   core.add_channel_member(c,z['id'],ch['id'])
  return web.json_response({'message':None,'invited_user':core.user_to_json(z)},status=201)
 elif b in ('/archive','/unarchive'):
  if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
  with core.tx() as c:
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(b=='/archive'),ch['id']));z=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
  return web.json_response({'message':None,'channel':core.channel_to_json(z)},status=201)
 elif b.startswith('/shrug'): b=(b[6:].strip()+' ¯\\_(ツ)_/¯').strip()
 elif b.startswith('/') and not b.startswith('/me'): return err(400,'unknown command')
 return web.json_response({'message':core.create_message(u,ch['id'],b,x.get('parent_id') if x else None,fids)},status=201)
async def messages(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 c=core.connect();ok=c.execute('SELECT 1 FROM memberships WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone();c.close()
 if not ok:return err(403,'forbidden')
 n,v=page(r.query,'messages');c=core.connect();rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND id>? ORDER BY id DESC LIMIT ?',(ch['id'],v,n+1)).fetchall();c.close();more=len(rows)>n;rows=rows[:n];return web.json_response({'messages':[core.message_to_json(z) for z in rows],'next_cursor':cur('messages',rows[-1]['id']) if more and rows else None})
async def reactions(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 m=core.connect().execute('SELECT * FROM messages WHERE id=?',(int(r.match_info['id']),)).fetchone()
 if not m:return err(404,'not found')
 x=await body(r);e=(x.get('emoji') if x else '') if r.method=='POST' else r.match_info.get('emoji','')
 if not isinstance(e,str) or not e:return err(400,'emoji')
 try:z=core.react(u,m['id'],e,r.method=='POST')
 except PermissionError:return err(403,'forbidden')
 return web.json_response({'reactions':z})
async def ws(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 w=web.WebSocketResponse();await w.prepare(r);cid=str(uuid.uuid4());core.touch_presence(cid,u['id']);seen={};gc=core.connect();gseen=gc.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0];gc.close();stop=False
 async def poll():
  nonlocal gseen
  while not w.closed:
   await asyncio.sleep(.05)
   gc=core.connect()
   for z in gc.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(gseen,)):
    gseen=z['id'];await w.send_json(json.loads(z['payload']))
   gc.close()
   for ch,last in list(seen.items()):
    for z in core.connect().execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)):
     p=json.loads(z[0]);seen[ch]=p['event_id'];await w.send_json(p)
 async def recv():
  async for m in w:
   if m.type==WSMsgType.TEXT:
    x=json.loads(m.data);ch=int(x.get('channel_id',0))
    if x['type']=='subscribe':seen[ch]=core.get_channel(ch)['last_event_id'];await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':seen[ch]})
    elif x['type']=='resume':seen[ch]=int(x.get('since_event_id',0));await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.get_channel(ch)['last_event_id']})
 task=asyncio.create_task(poll());await recv();task.cancel();core.drop_presence(cid);return w
async def root(r):return web.Response(text='<!doctype html><html><body><div data-testid="auth-form"><input name="username"><input name="password"><button data-testid="auth-submit" data-button-role="primary">Login</button></div></body></html>',content_type='text/html')
def make(n):
 a=web.Application();a['node']=n
 a.router.add_get('/api/health',health);a.router.add_get('/',root);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
 a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces/{slug}',ws_detail);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_post('/api/workspaces/{slug}/channels',create_ch);a.router.add_get('/api/workspaces/{slug}/groups',groups);a.router.add_post('/api/workspaces/{slug}/groups',groups);a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',groups);a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',groups)
 a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_post('/api/channels/{id}/messages',post_msg);a.router.add_get('/api/channels/{id}/messages',messages)
 a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_patch('/api/messages/{id}',edit);a.router.add_delete('/api/messages/{id}',delete_msg);a.router.add_post('/api/messages/{id}/reactions',reactions);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reactions);a.router.add_post('/api/dms',dms);a.router.add_post('/api/files',upload);a.router.add_get('/api/files/{id}',fileget);a.router.add_get('/api/files/{id}/download',fileget);a.router.add_patch('/api/users/me',profile);a.router.add_get('/api/ws',ws);a.router.add_get('/api/users/{id}',userget)
 return a
async def replies(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(r.match_info['id']),)).fetchone()
 if not m:return err(404,'not found')
 x=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(m['id'],)).fetchall();c.close();return web.json_response({'replies':[core.message_to_json(z) for z in x],'next_cursor':None})
async def edit(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 x=await body(r);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 if not m:return err(404,'not found')
 if m['author_id']!=u['id']:return err(403,'forbidden')
 if not isinstance(x.get('body') if x else None,str) or not x['body']:return err(400,'body')
 with core.tx() as c:c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(x['body'],core.now_iso(),m['id']));z=c.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone();core.emit_event(c,m['channel_id'],'message.updated',{'message':core.message_to_json(z)})
 return web.json_response({'message':core.message_to_json(z)})
async def delete_msg(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 if not m:return err(404,'not found')
 if m['author_id']!=u['id']:return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m['id'],));core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':m['id']})
 return web.json_response({'deleted':True})
async def dms(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 x=await body(r);oid=x.get('user_id') if x else None
 if not isinstance(oid,int):return err(400,'user_id')
 c=core.connect();o=c.execute('SELECT * FROM users WHERE id=?',(oid,)).fetchone();c.close()
 if not o:return err(404,'not found')
 return web.json_response({'channel_id':core.open_dm(u,oid)})
async def upload(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 try:
  q=await r.multipart();p=await q.next();data=await p.read()
  if len(data)>10485760:return err(413,'too large')
  z=core.save_file(u,p.filename or 'file',p.headers.get('Content-Type','application/octet-stream'),data);return web.json_response({'file':core.file_to_json(z)},status=201)
 except:return err(400,'file')
async def fileget(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();z=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 if not z:return err(404,'not found')
 if r.path.endswith('/download'):return web.Response(body=open('/app/data/files/'+str(z['id']),'rb').read(),content_type=z['content_type'],headers={'Content-Disposition':f'attachment; filename="{z["filename"]}"'})
 return web.json_response({'file':core.file_to_json(z)})
async def profile(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 x=await body(r)
 try:z=core.update_profile(u,x or {})
 except:return err(400,'invalid profile')
 return web.json_response({'user':core.user_to_json(z)})
async def userget(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();z=c.execute('SELECT * FROM users WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 return web.json_response({'user':core.user_to_json(z)}) if z else err(404,'not found')
async def groups(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();gs=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 if r.method=='GET':return web.json_response({'groups':[core.group_to_json(z) for z in gs]})
 if r.method=='DELETE':
  c=core.connect();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone();c.close()
  if g and g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
  with core.tx() as c:c.execute('DELETE FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle']))
  return web.json_response({'deleted':True})
 if r.method=='PATCH':
  x=await body(r);c=core.connect();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone();c.close()
  if not g:return err(404,'not found')
  if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
  with core.tx() as c:
   if 'name' in x:c.execute('UPDATE groups_ SET name=? WHERE id=?',(x['name'],g['id']))
   if 'user_ids' in x:
    for i in x['user_ids']:
     if not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],i)).fetchone():return err(400,'invalid member')
    c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
    for i in x['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],i))
   g=c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone()
  return web.json_response({'group':core.group_to_json(g)})
 x=await body(r)
 try:z=core.create_group(w['id'],u['id'],x['handle'],x.get('name',''),x.get('user_ids',[]))
 except FileExistsError:return err(409,'duplicate')
 except:return err(400,'invalid group')
 return web.json_response({'group':core.group_to_json(z)},status=201)
async def channel_members(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 c=core.connect()
 if not c.execute('SELECT 1 FROM memberships WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone():c.close();return err(403,'forbidden')
 rows=c.execute('SELECT u.* FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
 return web.json_response({'members':[core.user_to_json(z) for z in rows]})
async def patch_channel(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 x=await body(r)
 with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(x.get('topic',''),ch['id']));z=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
 return web.json_response({'channel':core.channel_to_json(z)})

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int);args=p.parse_args();n=args.node;core.init_db();web.run_app(make(n),host='127.0.0.1',port=8000+n)
