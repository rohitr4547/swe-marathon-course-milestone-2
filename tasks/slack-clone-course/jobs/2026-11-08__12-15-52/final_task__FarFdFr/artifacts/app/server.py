import sys,os,json,re,asyncio,uuid,base64,time
from zoneinfo import ZoneInfo
from aiohttp import web,WSMsgType
sys.path.insert(0,'/app');import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
def err(n,m): return web.json_response({'error':m},status=n)
async def read_body(r):
 try:
  x=await r.json();return x if isinstance(x,dict) else None
 except:return None
def auth(r):
 h=r.headers.get('Authorization','')
 t=h[7:] if h.startswith('Bearer ') else r.query.get('token','')
 return core.user_for_token(t)
def need(r):
 u=auth(r)
 if not u: raise web.HTTPUnauthorized()
 return u
def users(c,ids):
 return [core.user_to_json(c.execute('SELECT * FROM users WHERE id=?',(i,)).fetchone()) for i in ids]
def msgrow(c,r):
 j=core.message_to_json(r)
 j['mentions']=json.loads(r['body_mentions']) if 'body_mentions' in r.keys() and r['body_mentions'] else []
 return j
async def health(r): return web.json_response({'status':'ok','node_id':NODE})
async def register(r):
 x=await read_body(r)
 if not x or not isinstance(x.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',x['username']) or not isinstance(x.get('password'),str) or len(x['password'])<8:return err(400,'invalid')
 with core.tx() as c:
  try:cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(x['username'],core.hash_password(x['password']),x.get('display_name') or x['username']))
  except:return err(409,'duplicate')
  u=c.execute('SELECT * FROM users WHERE username=?',(x['username'],)).fetchone();t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
async def login(r):
 x=await read_body(r);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(x.get('username') if x else '',)).fetchone();c.close()
 if not u or not core.verify_password(x.get('password',''),u['password']):return err(401,'invalid')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=auth(r)
 return web.json_response({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspaces(r):
 u=need(r);c=core.connect();a=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return web.json_response({'workspaces':[core.workspace_to_json(x) for x in a]})
async def create_ws(r):
 u=need(r);x=await read_body(r)
 if not x or not re.fullmatch(r'[a-z0-9-]{2,32}',str(x.get('slug',''))) or not isinstance(x.get('name'),str):return err(400,'invalid')
 with core.tx() as c:
  try:cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],u['id']))
  except:return err(409,'duplicate')
  w=cur.lastrowid;ch=core.create_channel_row(c,w,'general');core.add_workspace_member(c,w,u['id'],'owner');core.add_channel_member(c,ch,u['id'])
  wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone();cr=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
 return web.json_response({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(cr)},status=201)
async def workspace(r):
 u=need(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect();chs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
 return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in chs],'read_state':{}})
async def create_channel(r):
 u=need(r);w=core.get_workspace(r.match_info['slug']);x=await read_body(r)
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 if not x or not re.fullmatch(r'[a-z0-9-]{1,32}',str(x.get('name',''))):return err(400,'invalid')
 with core.tx() as c:
  try:i=core.create_channel_row(c,w['id'],x['name'],bool(x.get('is_private')),x.get('topic',''));core.add_channel_member(c,i,u['id'])
  except:return err(409,'duplicate')
  ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
 return web.json_response({'channel':core.channel_to_json(ch)},status=201)
async def join(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 with core.tx() as c:
  if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
  core.add_channel_member(c,ch['id'],u['id'])
 return web.json_response({'channel':core.channel_to_json(ch)})
async def postmsg(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']));x=await read_body(r)
 if not ch:return err(404,'not found')
 if isinstance(x,dict) and isinstance(x.get('body'),str) and x['body'].strip() in ('/archive','/unarchive'):
  with core.tx() as c:
   w=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
   if not w or int(w['owner_id'])!=int(u['id']): return err(403,'forbidden')
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if x['body'].strip()=='/archive' else 0,ch['id']))
   q=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
  return web.json_response({'message':None,'channel':core.channel_to_json(q)},status=201)
 if isinstance(x,dict) and isinstance(x.get('body'),str) and x['body'].strip()=='/me':return err(400,'empty')
 command_request=isinstance(x,dict) and isinstance(x.get('body'),str) and x.get('body').strip().startswith('/')
 if not core.can_read_channel(u['id'],ch) and not command_request:return err(403,'forbidden')
 if ch['is_archived'] and not (isinstance(x,dict) and isinstance(x.get('body'),str) and x.get('body').startswith('/')):return err(423,'archived')
 if not x or not isinstance(x.get('body'),str) or not x['body'].strip() or x.get('body').strip()=='/me':return err(400,'empty')
 command_body=x['body']
 if command_body.startswith('/'):
  if command_body.startswith('//'):
   x['body']=command_body[1:]
  elif command_body.startswith('/me '):
   pass
  elif command_body=='/me':
   return err(400,'text required')
  elif command_body.startswith('/shrug'):
   rest=command_body[6:].strip();x['body']=(rest+' ' if rest else '')+'¯\\_(ツ)_/¯'
  elif command_body.startswith('/topic'):
   rest=command_body[6:].strip()
   if not rest:return err(400,'topic')
   with core.tx() as c:
    c.execute('UPDATE channels SET topic=? WHERE id=?',(rest,ch['id']))
    q=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
   return web.json_response({'message':None,'channel':core.channel_to_json(q)},status=201)
  elif command_body.startswith('/invite '):
   name=command_body[8:].strip()
   if not name.startswith('@'):return err(400,'invite')
   c=core.connect();target=c.execute('SELECT * FROM users WHERE username=?',(name[1:],)).fetchone();c.close()
   if not target:return err(400,'invite')
   with core.tx() as c:
    core.add_channel_member(c,ch['id'],target['id'])
   return web.json_response({'message':None,'invited_user':core.user_to_json(target)},status=201)
  elif command_body in ('/archive','/unarchive'):
   w=core.get_workspace(ch['workspace_id'])
   if not w or not (int(w['owner_id'])==int(u['id']) or core.member_role(w['id'],u['id'])=='owner'):return err(403,'forbidden')
   archived=1 if command_body=='/archive' else 0
   with core.tx() as c:
    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(archived,ch['id']))
    q=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
   return web.json_response({'message':None,'channel':core.channel_to_json(q)},status=201)
  else:
   return err(400,'unknown command')
 fids=x.get('file_ids',[])
 if not isinstance(fids,list) or any(not isinstance(i,int) for i in fids):return err(400,'file_ids')
 with core.tx() as c:
  for fid in fids:
   f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
   if not f or f['uploader_id']!=u['id'] or f['message_id'] is not None:return err(400,'file')
  mentions=[]
  for uname in re.findall(r'(?<![A-Za-z0-9_@])@([A-Za-z0-9_]+)',x['body']):
   z=c.execute('SELECT id FROM users WHERE username=?',(uname,)).fetchone()
   if z and z['id']!=u['id'] and core._cm(ch['id'],z['id']) and z['id'] not in mentions:mentions.append(z['id'])
  if '@here' in x['body']:
   for uid in core.present_user_ids():
    z=c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone()
    if z and uid!=u['id'] and uid not in mentions:mentions.append(uid)
  if '@channel' in x['body']:
   for z in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)):
    if z['user_id']!=u['id'] and z['user_id'] not in mentions:mentions.append(z['user_id'])
  for gh in re.findall(r'(?<![A-Za-z0-9_@])@([a-z0-9-]{1,32})',x['body']):
   g=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(ch['workspace_id'],gh)).fetchone() if ch['workspace_id'] else None
   if g:
    for z in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)):
     if z['user_id']!=u['id'] and core._cm(ch['id'],z['user_id']) and z['user_id'] not in mentions:mentions.append(z['user_id'])
  cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,body_mentions) VALUES(?,?,?,?,?,?)',(ch['id'],u['id'],x['body'],x.get('parent_id'),core.now_iso(),json.dumps(mentions)));i=cur.lastrowid
  for fid in fids:c.execute('UPDATE files SET message_id=? WHERE id=?',(i,fid))
  rr=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone();core.emit_event(c,ch['id'],'message.reply' if x.get('parent_id') else 'message.created',{'message':core.message_to_json(rr)})
 return web.json_response({'message':core.message_to_json(rr)},status=201)
async def messages(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 c=core.connect();a=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT 200',(ch['id'],)).fetchall();c.close();return web.json_response({'messages':[core.message_to_json(x) for x in a],'next_cursor':None})
async def channel_members(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 c=core.connect();rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=? ORDER BY u.id',(ch['id'],)).fetchall();c.close()
 return web.json_response({'members':[core.user_to_json(x) for x in rows]})
async def replies(r):
 u=need(r);c=core.connect();a=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(r.match_info['id'],)).fetchall();c.close();return web.json_response({'replies':[core.message_to_json(x) for x in a],'next_cursor':None})
async def react(r):
 u=need(r);mid=int(r.match_info['id']);x=await read_body(r);emoji=x.get('emoji') if x else ''
 if not isinstance(emoji,str) or not emoji:return err(400,'emoji')
 with core.tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not m:return err(404,'not found')
  ch=core.get_channel(m['channel_id'])
  if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
  old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,u['id'])).fetchone()
  if not old:c.execute('INSERT INTO reactions VALUES(?,?,?,?)',(mid,emoji,u['id'],core.now_iso()));core.emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
 return web.json_response({'reactions':core.reactions_for(mid)})
async def delreact(r):
 u=need(r);mid=int(r.match_info['id']);emoji=r.match_info['emoji']
 with core.tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not m:return err(404,'not found')
  ch=core.get_channel(m['channel_id'])
  if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
  if ch['is_archived']:return err(423,'archived')
  q=c.execute('DELETE FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,u['id']))
  if q.rowcount: core.emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
 return web.json_response({'reactions':core.reactions_for(mid)})
async def userprofile(r):
 u=need(r);c=core.connect();x=c.execute('SELECT * FROM users WHERE id=?',(r.match_info['id'],)).fetchone();c.close();return web.json_response({'user':core.user_to_json(x)}) if x else err(404,'not found')
async def ws(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 conn_id=uuid.uuid4().hex
 core.touch_presence(conn_id,u['id'])
 w=web.WebSocketResponse();await w.prepare(r);subs={};running=True
 async def deliver():
  sent={}; global_sent=0
  while not w.closed:
   c=core.connect(); grows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_sent,)).fetchall();c.close()
   for g in grows:
    await w.send_str(g['payload']);global_sent=g['id']
   for ch in list(subs):
    c=core.connect();rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,sent.get(ch,subs[ch]))).fetchall();c.close()
    for z in rows:
     await w.send_str(z['payload']);sent[ch]=z['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(deliver())
 try:
  async for m in w:
   if m.type==WSMsgType.TEXT:
    x=json.loads(m.data);ch=int(x.get('channel_id',0))
    if x.get('type')=='subscribe':
     c=core.connect();head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0];c.close();subs[ch]=head;await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
    elif x.get('type')=='resume':
     since=int(x.get('since_event_id',0));subs[ch]=since;c=core.connect();head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0];c.close();await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':head})
 finally:
  task.cancel();core.drop_presence(conn_id)
 return w

async def dms(r):
 u=need(r);x=await read_body(r)
 if not x or not isinstance(x.get('user_id'),int):return err(400,'user_id')
 i=core.open_dm(u,x['user_id'])
 return err(404,'not found') if i is None else web.json_response({'channel_id':i})
async def upload(r):
 u=need(r)
 try:
  rd=await r.multipart();part=await rd.next()
  if not part or part.name!='file':return err(400,'file')
  data=await part.read()
 except:return err(400,'file')
 if len(data)>10*1024*1024:return err(413,'too large')
 f=core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)
 return web.json_response({'file':core.file_to_json(f)},status=201)
async def getfile(r):
 u=need(r);c=core.connect();f=c.execute('SELECT * FROM files WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 return err(404,'not found') if not f else web.json_response({'file':core.file_to_json(f)})
async def download(r):
 u=need(r);c=core.connect();f=c.execute('SELECT * FROM files WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not f:return err(404,'not found')
 return web.Response(body=open('/app/data/files/'+str(f['id']),'rb').read(),content_type=f['content_type'],headers={'Content-Disposition':'attachment; filename="'+f['filename']+'"'})
async def groups(r):
 u=need(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();a=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 if r.method=='GET':return web.json_response({'groups':[core.group_to_json(x) for x in a]})
 x=await read_body(r)
 if not x or not isinstance(x.get('handle'),str) or not isinstance(x.get('name'),str) or not isinstance(x.get('user_ids'),list):return err(400,'invalid')
 try:g=core.create_group(u,w['id'],x['handle'],x['name'],x['user_ids'])
 except FileExistsError:return err(409,'duplicate')
 except:return err(400,'invalid')
 return web.json_response({'group':core.group_to_json(g)},status=201)
async def group_edit(r):
 u=need(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 x=await read_body(r) or {}
 try:g=core.update_group(u,w['id'],r.match_info['handle'],x.get('name'),x.get('user_ids'))
 except PermissionError:return err(403,'forbidden')
 except:return err(400,'invalid')
 return err(404,'not found') if not g else web.json_response({'group':core.group_to_json(g)})
async def group_delete(r):
 u=need(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 with core.tx() as c:
  g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone()
  if not g:return web.json_response({'deleted':True})
  if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
  c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],))
 return web.json_response({'deleted':True})
async def missing_auth_route(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 return err(404,'not found')
async def profile_patch(r):
 u=need(r);x=await read_body(r)
 if not isinstance(x,dict):return err(400,'invalid')
 allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
 if any(k not in allowed or not isinstance(v,str) for k,v in x.items()):return err(400,'invalid')
 limits={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512)}
 for k,(lo,hi) in limits.items():
  if k in x and not lo<=len(x[k])<=hi:return err(400,'invalid')
 if 'timezone' in x:
  try: ZoneInfo(x['timezone'])
  except: return err(400,'invalid')
 with core.tx() as c:
  for k,v in x.items(): c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
  q=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
  core.emit_global(c,{'type':'user.updated','user':core.user_to_json(q)})
 return web.json_response({'user':core.user_to_json(q)})
app=web.Application()
for p,h in [('/api/health',health),('/api/auth/register',register),('/api/auth/login',login),('/api/auth/me',me),('/api/workspaces',workspaces),('/api/workspaces',create_ws),('/api/workspaces/{slug}',workspace),('/api/workspaces/{slug}/channels',create_channel),('/api/channels/{id}/join',join),('/api/channels/{id}/messages',postmsg),('/api/channels/{id}/messages',messages),('/api/messages/{id}/replies',replies),('/api/messages/{id}/reactions',react),('/api/users/{id}',userprofile),('/api/ws',ws)]:app.router.add_route('POST' if h in (register,login,create_ws,create_channel,join,postmsg,react) else 'GET',p,h)
app.router.add_delete('/api/messages/{id}/reactions/{emoji}',delreact)
app.router.add_get('/api/channels/{id}/members',channel_members)
app.router.add_post('/api/dms',dms)
app.router.add_post('/api/files',upload)
app.router.add_get('/api/files/{id}',getfile)
app.router.add_get('/api/files/{id}/download',download)
app.router.add_get('/api/workspaces/{slug}/groups',groups)
app.router.add_post('/api/workspaces/{slug}/groups',groups)
app.router.add_patch('/api/workspaces/{slug}/groups/{handle}',group_edit)
app.router.add_delete('/api/workspaces/{slug}/groups/{handle}',group_delete)
app.router.add_patch('/api/users/me',profile_patch)
app.router.add_get('/',lambda r:web.Response(text='<html><body>Huddle</body></html>',content_type='text/html'))
web.run_app(app,host='127.0.0.1',port=8000+NODE)
