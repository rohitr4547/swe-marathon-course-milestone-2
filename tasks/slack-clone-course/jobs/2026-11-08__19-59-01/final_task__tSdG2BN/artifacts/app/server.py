import asyncio,json,argparse,re,sys,os,uuid,time
from aiohttp import web,WSMsgType
import core
NODE=0
def out(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return out({'error':msg},status)
async def body(req):
 try:
  x=await req.json(); return x if isinstance(x,dict) else None
 except: return None
def user(req):
 h=req.headers.get('Authorization','')
 return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
async def auth(req):
 u=user(req)
 if not u: raise web.HTTPUnauthorized(text=json.dumps({'error':'unauthorized'}),content_type='application/json')
 return u
def valid_username(x): return isinstance(x,str) and re.fullmatch(r'[A-Za-z0-9_]{1,32}',x)
def valid_slug(x): return isinstance(x,str) and re.fullmatch(r'[a-z0-9-]{2,32}',x)
async def health(req): return out({'status':'ok','node_id':NODE})
async def register(req):
 d=await body(req)
 if not d or not valid_username(d.get('username')) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid registration')
 with core.tx() as c:
  try:
   r=c.execute("INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?) RETURNING *",(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username'],core.now_iso())).fetchone()
  except: return err(409,'duplicate username')
  t=core.issue_token(c,r['id'])
 return out({'user':core.user_to_json(r),'token':t},201)
async def login(req):
 d=await body(req)
 if not d:return err(400,'invalid body')
 c=core.connect();r=c.execute('SELECT * FROM users WHERE username=?',(d.get('username'),)).fetchone();c.close()
 if not r or not core.verify_password(d.get('password',''),r['password']):return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,r['id'])
 return out({'user':core.user_to_json(r),'token':t})
async def me(req): return out({'user':core.user_to_json(await auth(req))})
async def workspaces(req):
 u=await auth(req);c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return out({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_workspace(req):
 u=await auth(req);d=await body(req)
 if not d or not valid_slug(d.get('slug')) or not isinstance(d.get('name'),str):return err(400,'invalid workspace')
 with core.tx() as c:
  try:w=c.execute("INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?) RETURNING *",(d['slug'],d['name'],u['id'])).fetchone()
  except:return err(409,'duplicate workspace')
  core.add_workspace_member(c,w['id'],u['id'],'owner')
  g=core.create_channel_row(c,w['id'],'general');core.add_channel_member(c,g['id'],u['id'])
 return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(g)},201)
async def workspace(req):
 u=await auth(req);w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect();chs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
 return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in chs],'read_state':[]})
async def create_channel(req):
 u=await auth(req);w=core.get_workspace(req.match_info['slug']);d=await body(req)
 if not w:return err(404,'not found')
 caller_role=core.member_role(w['id'],u['id'])
 if caller_role not in ('owner','admin','member'):return err(403,'forbidden')
 if d and d.get('is_private') and caller_role not in ('owner','admin'):return err(403,'forbidden')
 if not d or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']) or not isinstance(d.get('topic',''),str) or len(d.get('topic',''))>250:return err(400,'invalid channel')
 with core.tx() as c:
  if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return err(409,'duplicate channel')
  r=core.create_channel_row(c,w['id'],d['name'],d.get('is_private',False),d.get('topic',''));core.add_channel_member(c,r['id'],u['id'])
 return out({'channel':core.channel_to_json(r)},201)
async def join(req):
 u=await auth(req);ch=core.get_channel(req.match_info['id'])
 if not ch:return err(404,'not found')
 with core.tx() as c:
  if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
   if w and w['join_mode']=='open' and not ch['is_private']: core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
  core.add_channel_member(c,ch['id'],u['id'])
 return out({'channel':core.channel_to_json(ch)})
async def get_user(req):
 u=await auth(req); c=core.connect(); r=c.execute('SELECT * FROM users WHERE id=?',(req.match_info['id'],)).fetchone(); c.close()
 if not r:return err(404,'user not found')
 return out({'user':core.user_to_json(r)})
async def patch_me(req):
 u=await auth(req); d=await body(req)
 if d is None:return err(400,'invalid body')
 allowed=('display_name','timezone','avatar_url','status_text','status_emoji')
 for k in d:
  if k not in allowed:return err(400,'invalid field')
  limits={'display_name':80,'status_text':140,'status_emoji':32,'avatar_url':512}
  if not isinstance(d[k],str) or len(d[k])==0 or len(d[k])>limits.get(k,250):return err(400,'invalid field')
 if 'timezone' in d:
  try:
   from zoneinfo import ZoneInfo
   ZoneInfo(d['timezone'])
  except: return err(400,'invalid timezone')
 sets=[k+'=?' for k in d]; vals=[d[k] for k in d]
 with core.tx() as c:
  if sets:r=c.execute('UPDATE users SET '+','.join(sets)+' WHERE id=? RETURNING *',(*vals,u['id'])).fetchone()
  else:r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
  if sets:core.emit_global(c,{'type':'user.updated','user':core.user_to_json(r)})
 return out({'user':core.user_to_json(r)})
async def groups(req):
 u=await auth(req); w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect(); rs=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
 return out({'groups':[{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'])} for r in rs]})
async def create_group(req):
 u=await auth(req);w=core.get_workspace(req.match_info['slug']);d=await body(req)
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 if not d or not isinstance(d.get('handle'),str) or not re.fullmatch(r'[a-z0-9_-]{1,32}',d['handle']) or not isinstance(d.get('name'),str) or not isinstance(d.get('user_ids',[]),list):return err(400,'invalid group')
 ids=d.get('user_ids',[])
 c=core.connect()
 for uid in ids:
  if not isinstance(uid,int) or not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone():c.close();return err(400,'invalid member')
 if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],d['handle'])).fetchone():c.close();return err(409,'duplicate handle')
 c.close()
 with core.tx() as c:r=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?) RETURNING *',(w['id'],d['handle'],d['name'],u['id'],json.dumps(ids))).fetchone()
 return out({'group':{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}},201)
async def update_group(req):
 u=await auth(req);w=core.get_workspace(req.match_info['slug']);d=await body(req);h=req.match_info['handle']
 if not w:return err(404,'not found')
 c=core.connect();r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone();c.close()
 if not r:return err(404,'not found')
 if r['creator_id']!=u['id'] and core.member_role(w['id'],u['id'])!='owner':return err(403,'forbidden')
 ids=d.get('user_ids',json.loads(r['user_ids'])) if d else json.loads(r['user_ids'])
 with core.tx() as c:x=c.execute('UPDATE groups_ SET name=COALESCE(?,name),user_ids=? WHERE id=? RETURNING *',(d.get('name') if d else None,json.dumps(ids),r['id'])).fetchone()
 return out({'group':{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':ids}})
async def delete_group(req):
 u=await auth(req);w=core.get_workspace(req.match_info['slug']);c=core.connect();r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone();c.close()
 if not r:return out({'deleted':True})
 if r['creator_id']!=u['id'] and core.member_role(w['id'],u['id'])!='owner':return err(403,'forbidden')
 with core.tx() as c:c.execute('DELETE FROM groups_ WHERE id=?',(r['id'],))
 return out({'deleted':True})
async def channel_members(req):
 u=await auth(req); ch=core.get_channel(req.match_info['id'])
 if not ch:return err(404,'not found')
 c=core.connect(); ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
 if not ok:c.close();return err(403,'forbidden')
 rs=c.execute('SELECT u.* FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall();c.close()
 return out({'members':[core.user_to_json(x) for x in rs]})
async def replies(req):
 u=await auth(req); pid=int(req.match_info['id']); c=core.connect()
 parent=c.execute('SELECT * FROM messages WHERE id=?',(pid,)).fetchone()
 if not parent:c.close();return err(404,'not found')
 ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(parent['channel_id'],u['id'])).fetchone()
 if not ok:c.close();return err(403,'forbidden')
 rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id ASC',(pid,)).fetchall();c.close()
 return out({'replies':[core.message_to_json(x) for x in rs],'next_cursor':None})
async def messages(req):
 u=await auth(req);ch=core.get_channel(req.match_info['id'])
 if not ch:return err(404,'not found')
 c=core.connect(); allowed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
 if not allowed: c.close(); return err(403,'forbidden')
 rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC LIMIT 200',(ch['id'],)).fetchall();c.close()
 return out({'messages':[core.message_to_json(x) for x in rs],'next_cursor':None})
async def post_message(req):
 u=await auth(req);ch=core.get_channel(req.match_info['id']);d=await body(req)
 if not ch:return err(404,'not found')
 c=core.connect(); allowed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.close()
 if not allowed:return err(403,'forbidden')
 if ch['is_archived'] and not (isinstance(d,dict) and isinstance(d.get('body'),str) and d['body'].strip()=='/unarchive'):return err(423,'channel archived')
 if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'empty message')
 text=d['body']
 if text.startswith('//'): text=text[1:]
 elif text.startswith('/'):
  parts=text.split(' ',1); cmd=parts[0][1:]; arg=parts[1] if len(parts)>1 else ''
  if cmd=='me':
   if not arg.strip():return err(400,'text required')
  elif cmd=='shrug':
   text=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
  elif cmd=='topic':
   if not arg.strip():return err(400,'topic required')
   with core.tx() as c:r=c.execute('UPDATE channels SET topic=? WHERE id=? RETURNING *',(arg,ch['id'])).fetchone()
   return out({'message':None,'channel':core.channel_to_json(r)},201)
  elif cmd=='invite':
   if not arg.startswith('@'):return err(400,'user required')
   name=arg.split()[0][1:]; c=core.connect(); target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone();c.close()
   if not target:return err(404,'user not found')
   with core.tx() as c: core.add_channel_member(c,ch['id'],target['id'])
   return out({'message':None,'invited_user':core.user_to_json(target)},201)
  elif cmd in ('archive','unarchive'):
   if core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
   with core.tx() as c:r=c.execute('UPDATE channels SET is_archived=? WHERE id=? RETURNING *',(1 if cmd=='archive' else 0,ch['id'])).fetchone()
   return out({'message':None,'channel':core.channel_to_json(r)},201)
  else:return err(400,'unknown command')
 fids=d.get('file_ids',[])
 if not isinstance(fids,list):return err(400,'invalid files')
 for fid in fids:
  if not isinstance(fid,int):return err(400,'invalid file')
  c=core.connect(); f=c.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone(); c.close()
  if not f:return err(400,'invalid file')
 r=core.create_message(u,ch['id'],text,d.get('parent_id'))
 if fids:
  with core.tx() as c:
   for fid in fids:c.execute('UPDATE files SET message_id=? WHERE id=?',(r['id'],fid))
 mj=core.message_to_json(r)
 c=core.connect(); members=[x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)).fetchall()]
 ids=set()
 for name in re.findall(r'(?<![\\w@])@([A-Za-z0-9_][A-Za-z0-9_-]*)',text):
  if name=='channel': ids.update(members)
  elif name=='here': ids.update(core.present_user_ids() if hasattr(core,'present_user_ids') else [])
  else:
   x=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
   if x: ids.add(x['id'])
   g=c.execute('SELECT user_ids FROM groups_ WHERE workspace_id=? AND handle=?',(ch['workspace_id'],name)).fetchone()
   if g: ids.update(json.loads(g['user_ids']))
 ids.discard(u['id']); ids.intersection_update(members); c.close(); mj['mentions']=sorted(ids)
 return out({'message':mj},201)
async def react(req):
 u=await auth(req); d=await body(req); mid=int(req.match_info['id'])
 if not d or not isinstance(d.get('emoji'),str) or not d['emoji']: return err(400,'invalid emoji')
 c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
 if not m: c.close(); return err(404,'message not found')
 ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
 member=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.close()
 if not member:return err(403,'forbidden')
 with core.tx() as c:
  exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],d['emoji'])).fetchone()
  if not exists:
   c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],d['emoji']))
   core.emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':d['emoji'],'user_id':u['id']})
 return out({'reactions':core.reactions_for(mid)})
async def remove_react(req):
 u=await auth(req);mid=int(req.match_info['id']);emoji=req.match_info['emoji']
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
 if not m:c.close();return err(404,'message not found')
 ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone();member=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone();c.close()
 if not member:return err(403,'forbidden')
 with core.tx() as c:
  n=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).rowcount
  if n:core.emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
 return out({'reactions':core.reactions_for(mid)})
async def unimplemented_auth_route(req):
 await auth(req)
 return err(404,'not found')
async def create_dm(req):
 u=await auth(req); d=await body(req)
 if not d or not isinstance(d.get('user_id'),int): return err(400,'invalid user_id')
 c=core.connect(); other=c.execute('SELECT * FROM users WHERE id=?',(d['user_id'],)).fetchone(); c.close()
 if not other:return err(404,'user not found')
 a1,b1=sorted((u['id'],other['id']))
 with core.tx() as c:
  row=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a1,b1)).fetchone()
  if row: cid=row['channel_id']
  else:
   r=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1) RETURNING id',( )).fetchone();cid=r['id']
   c.execute('INSERT INTO dms VALUES(?,?,?)',(a1,b1,cid));core.add_channel_member(c,cid,a1);core.add_channel_member(c,cid,b1)
 return out({'channel_id':cid})
async def upload_file(req):
 u=await auth(req)
 try:
  reader=await req.multipart(); part=await reader.next()
  if not part or part.name!='file':return err(400,'file required')
  data=await part.read(decode=False)
 except:return err(400,'invalid upload')
 if len(data)>10*1024*1024:return err(413,'file too large')
 ctype=part.headers.get('Content-Type','application/octet-stream');name=part.filename or 'file'
 with core.tx() as c:
  r=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?) RETURNING *',(u['id'],name,ctype,len(data),core.now_iso())).fetchone()
  os.makedirs('/app/data/files',exist_ok=True)
  open('/app/data/files/'+str(r['id']),'wb').write(data)
 return out({'file':dict(r)},201)
async def get_file(req):
 u=await auth(req);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
 if not r:return err(404,'file not found')
 return out({'file':dict(r)})
async def download_file(req):
 u=await auth(req);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
 if not r:return err(404,'file not found')
 try:data=open('/app/data/files/'+str(r['id']),'rb').read()
 except:return err(404,'file not found')
 return web.Response(body=data,content_type=r['content_type'],headers={'Content-Disposition':'attachment; filename="'+r['filename']+'"'})
async def ws(req):
 t=req.query.get('token','');u=core.user_for_token(t)
 if not u:return err(401,'unauthorized')
 w=web.WebSocketResponse();await w.prepare(req); conn_id=uuid.uuid4().hex
 with core.tx() as c:c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,u['id'],time.time()))
 last={}; gc=core.connect(); grow=gc.execute('SELECT COALESCE(MAX(id),0) n FROM global_events').fetchone()['n']; gc.close()
 async def poll():
  nonlocal grow
  while not w.closed:
   for ch,s in list(last.items()):
    for e in core.events_after(ch,s): await w.send_json(e);last[ch]=e['event_id']
   gc=core.connect(); gs=gc.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(grow,)).fetchall(); gc.close()
   for g in gs: await w.send_json(json.loads(g['payload']))
   if gs: grow=gs[-1]['id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for m in w:
   if m.type==WSMsgType.TEXT:
    d=json.loads(m.data)
    if d.get('type') in ('subscribe','resume'):
     ch=int(d['channel_id']); head=core.head_event_id(ch)
     if d['type']=='subscribe':
      last[ch]=head
      await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
     else:
      since=int(d.get('since_event_id',0))
      for e in core.events_after(ch,since): await w.send_json(e)
      last[ch]=head
      await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':head})
 finally:
  task.cancel()
  with core.tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
 return w

async def patch_workspace(req):
 u=await auth(req);w=core.get_workspace(req.match_info['slug']);d=await body(req)
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 if not d or (('join_mode' in d) and d['join_mode'] not in ('open','invite_only')):return err(400,'invalid workspace')
 with core.tx() as c:
  r=c.execute('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=? RETURNING *',(d.get('name'),d.get('join_mode'),w['id'])).fetchone()
 return out({'workspace':core.workspace_to_json(r)})
async def workspace_members(req):
 u=await auth(req); w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 return out({'members':[dict(x) for x in rs]})
async def set_role(req):
 u=await auth(req);w=core.get_workspace(req.match_info['slug']); tid=int(req.match_info['user_id']);d=await body(req)
 if not w:return err(404,'not found')
 role=core.member_role(w['id'],u['id']); old=core.member_role(w['id'],tid)
 if role not in ('owner','admin'):return err(403,'forbidden')
 if not d or d.get('role') not in ('admin','member','guest'):return err(400,'invalid role')
 if not old:return err(404,'member not found')
 if tid==w['owner_id'] or (old=='admin' and role!='owner'):return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(d['role'],w['id'],tid))
 return out({'member':{'user_id':tid,'role':d['role']}})
async def patch_channel(req):
 u=await auth(req);ch=core.get_channel(req.match_info['id']);d=await body(req)
 if not ch:return err(404,'not found')
 if not core.member_role(ch['workspace_id'],u['id']):return err(403,'forbidden')
 if not d or not isinstance(d.get('topic',''),str) or len(d.get('topic',''))>250:return err(400,'invalid topic')
 with core.tx() as c:r=c.execute('UPDATE channels SET topic=? WHERE id=? RETURNING *',(d.get('topic',''),ch['id'])).fetchone()
 return out({'channel':core.channel_to_json(r)})
async def archive(req):
 u=await auth(req);ch=core.get_channel(req.match_info['id'])
 if not ch:return err(404,'not found')
 w=core.get_workspace(req.match_info.get('slug','')) if req.match_info.get('slug') else None
 if core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
 with core.tx() as c:r=c.execute('UPDATE channels SET is_archived=? WHERE id=? RETURNING *',(1 if req.path.endswith('archive') else 0,ch['id'])).fetchone()
 return out({'channel':core.channel_to_json(r)})
async def edit_message(req):
 u=await auth(req);d=await body(req);c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone()
 if not r:c.close();return err(404,'not found')
 ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone();c.close()
 if r['deleted']:return err(409,'message deleted')
 if r['author_id']!=u['id']:return err(403,'forbidden')
 if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'invalid body')
 with core.tx() as c:
  r=c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=? RETURNING *',(d['body'],core.now_iso(),r['id'])).fetchone()
  core.emit_event(c,ch['id'],'message.updated',{'message':core.message_to_json(r)})
 mj=core.message_to_json(r)
 c=core.connect(); members=[x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)).fetchall()]
 ids=set()
 for name in re.findall(r'(?<![\\w@])@([A-Za-z0-9_][A-Za-z0-9_-]*)',d['body']):
  x=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
  if x: ids.add(x['id'])
 ids.discard(u['id']); ids.intersection_update(members); c.close(); mj['mentions']=sorted(ids)
 return out({'message':mj})
async def delete_message(req):
 u=await auth(req);c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone()
 if not r:c.close();return err(404,'not found')
 ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone();role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone() if ch['workspace_id'] else None;c.close()
 if r['deleted']:return out({'deleted':True})
 if r['author_id']!=u['id'] and (not role or role['role']!='owner'):return err(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],))
  core.emit_event(c,ch['id'],'message.deleted',{'message_id':r['id']})
 return out({'deleted':True})

def app():
 a=web.Application();a.router.add_get('/',lambda req:web.FileResponse('/app/static/index.html'));a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_post('/api/workspaces/{slug}/channels',create_channel);a.router.add_patch('/api/workspaces/{slug}',patch_workspace);a.router.add_get('/api/workspaces/{slug}/members',workspace_members);a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',set_role);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_post('/api/channels/{id}/archive',archive);a.router.add_post('/api/channels/{id}/unarchive',archive);a.router.add_patch('/api/messages/{id}',edit_message);a.router.add_delete('/api/messages/{id}',delete_message);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',post_message);a.router.add_post('/api/messages/{id}/reactions',react);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',remove_react);a.router.add_get('/api/users/{id}',get_user);a.router.add_patch('/api/users/me',patch_me);a.router.add_get('/api/workspaces/{slug}/groups',groups);a.router.add_post('/api/workspaces/{slug}/groups',create_group);a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',update_group);a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',delete_group);a.router.add_post('/api/dms',create_dm);a.router.add_post('/api/files',upload_file);a.router.add_get('/api/files/{id}',get_file);a.router.add_get('/api/files/{id}/download',download_file);a.router.add_get('/api/ws',ws);return a
if __name__=='__main__':
 core.init_db();p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;web.run_app(app(),host='127.0.0.1',port=8000+NODE)
