import sys,json,re,asyncio,os,time,secrets
from aiohttp import web
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(req):
 try:
  x=await req.json(); return x if isinstance(x,dict) else None
 except: return None
async def require_user(req):
 h=req.headers.get('Authorization','')
 if not h.startswith('Bearer '): return None
 return core.user_for_token(h[7:])
def ok(x,status=200): return web.json_response(x,status=status)
async def health(req): return ok({'status':'ok','node_id':NODE})
async def register(req):
 b=await read_json(req)
 if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid registration')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
   u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
  return ok({'user':core.user_to_json(u),'token':t},201)
 except: return json_error(409,'username exists')
async def login(req):
 b=await read_json(req); c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if b else '',)).fetchone();c.close()
 if not u or not isinstance(b.get('password') if b else None,str) or not core.verify_password(b['password'],u['password']):return json_error(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return ok({'user':core.user_to_json(u),'token':t})
async def me(req):
 u=await require_user(req)
 return ok({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def workspaces(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 if req.method=='GET':
  c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],));x=[core.workspace_to_json(r) for r in rows];c.close();return ok({'workspaces':x})
 b=await read_json(req)
 if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')) or not isinstance(b.get('name'),str):return json_error(400,'invalid workspace')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']));w= c.execute('SELECT last_insert_rowid()').fetchone()[0]
   core.add_workspace_member(c,w,u['id'],'owner');ch=core.create_channel_row(c,w,'general')
   core.add_channel_member(c,ch,u['id']); wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone();cr=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  return ok({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(cr)},201)
 except: return json_error(409,'workspace exists')
async def workspace(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return json_error(404,'not found')
 if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],req.query.get('include_archived') in ('1','true'))); chs=[core.channel_to_json(r) for r in rows if not r['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['id'],u['id'])).fetchone()]; c.close()
 states=[]
 for cc in chs:
  c2=core.connect(); rr=c2.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],cc['id'])).fetchone(); last=rr['last_read_event_id'] if rr else 0
  evrows=c2.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cc['id'],last)).fetchall(); unread=0; mentions=0
  for er in evrows:
   z=json.loads(er['payload']); mid=z.get('message_id') or (z.get('message') or {}).get('id')
   if mid:
    mm=c2.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
    if mm and mm['author_id']!=u['id']:
     unread+=1
     if u['id'] in core.mention_ids(mid,mm['body'],cc['id'],mm['author_id']): mentions+=1
  c2.close(); states.append({'channel_id':cc['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':mentions})
 return ok({'workspace':core.workspace_to_json(w),'channels':chs,'read_state':states})
async def members(req):
 u=await require_user(req);w=core.get_workspace(req.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT m.user_id,m.role,u.username,u.display_name FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],));x=[dict(r) for r in rs];c.close();return ok({'members':x})
async def channels(req):
 u=await require_user(req);w=core.get_workspace(req.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if not role:return json_error(403,'forbidden')
 b=await read_json(req)
 if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']) or ('topic' in b and not isinstance(b.get('topic'),str)) or len(b.get('topic',''))>250:return json_error(400,'invalid channel')
 if role=='guest' or (b.get('is_private') and role not in ('owner','admin')):return json_error(403,'forbidden')
 try:
  with core.tx() as c:
   ch=core.create_channel_row(c,w['id'],b['name'],b.get('is_private',False),b.get('topic',''));core.add_channel_member(c,ch,u['id']);r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  return ok({'channel':core.channel_to_json(r)},201)
 except:return json_error(409,'channel exists')
async def channel_manage(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return json_error(404,'not found')
 if req.method=='GET':
  if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
  c=core.connect(); rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)); x=[core.user_to_json(r) for r in rs]; c.close()
  return ok({'members':x})
 if req.method=='PATCH':
  c0=core.connect(); ismem=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
  if not ismem and (not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner'):return json_error(403,'forbidden')
  if ch['is_archived']:return json_error(423,'archived')
  b=await read_json(req)
  if not b or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return json_error(400,'invalid topic')
  with core.tx() as c:
   c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id'])); core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())}); r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
  return ok({'channel':core.channel_to_json(r)})
 action=req.match_info.get('action')
 if req.method=='POST' and action=='leave':
  with core.tx() as c:
   gone=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).rowcount
   if gone: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
   r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
  return ok({'channel':core.channel_to_json(r)})
 if req.method=='POST' and action in ('archive','unarchive'):
  c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c.close()
  if not w or u['id']!=w['owner_id']:return json_error(403,'forbidden')
  with core.tx() as c:
   old=ch['is_archived']; val=1 if action=='archive' else 0; c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
   if old != val: core.emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':core.channel_to_json(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
   r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
  return ok({'channel':core.channel_to_json(r)})
 return json_error(404,'not found')
async def channel_join(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return json_error(404,'not found')
 if ch['is_private'] or (ch['workspace_id'] and core.member_role(ch['workspace_id'],u['id'])=='guest') or (ch['workspace_id'] and core.member_role(ch['workspace_id'],u['id']) is None and core.get_workspace((core.connect().execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() or {'slug':''})['slug'])['join_mode']=='invite_only'):return json_error(403,'forbidden')
 with core.tx() as c:
  if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):
   core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
  was=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
  core.add_channel_member(c,ch['id'],u['id'])
  if not was: core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
  r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
 return ok({'channel':core.channel_to_json(r)})

async def message_edit_delete(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 mid=int(req.match_info['id']); c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
 if not m:c.close();return json_error(404,'not found')
 ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone();w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch['workspace_id'] else None;c.close()
 owner=bool(w and w['owner_id']==u['id'])
 if req.method=='PATCH':
  if m['deleted']:return json_error(409,'deleted')
  if m['author_id']!=u['id']:return json_error(403,'forbidden')
  b=await read_json(req)
  if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'invalid body')
  with core.tx() as c:
   edited=core.now_iso();c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],edited,mid));r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   core.emit_event(c,m['channel_id'],'message.updated',{'message':core.message_to_json(r)})
  return ok({'message':core.message_to_json(r)})
 if m['author_id']!=u['id'] and not owner:return json_error(403,'forbidden')
 if not m['deleted']:
  with core.tx() as c:
   c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
 return ok({'deleted':True})

async def messages(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return json_error(404,'not found')
 if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
 if req.method=='GET':
  try:n,cur=core.parse_paging(req.query,'messages')
  except:return json_error(400,'paging')
  c=core.connect()
  if cur:
   rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL AND id<? ORDER BY id DESC LIMIT ?',(ch['id'],cur,n+1))
  else:
   rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT ?',(ch['id'],n+1))
  rows=list(rs); more=len(rows)>n; rows=rows[:n]; x=[core.message_to_json(r) for r in rows]; c.close()
  return ok({'messages':x,'next_cursor':core.encode_cursor('messages',rows[-1]['id']) if more and rows else None})
 b=await read_json(req)
 if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty body')
 body=b['body']
 if body.startswith('/') and not body.startswith('//'):
  parts=body.split(' ',1); cmd=parts[0]; arg=parts[1].strip() if len(parts)>1 else ''
  if cmd=='/me':
   if not arg:return json_error(400,'missing text')
   body=body
  elif cmd=='/shrug':
   body=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
  elif cmd=='/topic':
   if not arg:return json_error(400,'missing topic')
   if len(arg)>250:return json_error(400,'invalid topic')
   if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
   with core.tx() as c:
    c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id'])); r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
   return ok({'message':None,'channel':core.channel_to_json(r)},201)
  elif cmd=='/invite':
   if not arg.startswith('@') or len(arg)<2:return json_error(400,'invalid invite')
   c=core.connect(); target=c.execute('SELECT * FROM users WHERE username=?',(arg[1:],)).fetchone();c.close()
   if not target:return json_error(404,'user not found')
   with core.tx() as c: core.add_channel_member(c,ch['id'],target['id'])
   return ok({'message':None,'invited_user':core.user_to_json(target)},201)
  elif cmd in ('/archive','/unarchive'):
   c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
   if not w or u['id']!=w['owner_id']:return json_error(403,'forbidden')
   with core.tx() as c:
    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if cmd=='/archive' else 0,ch['id']));r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
   return ok({'message':None,'channel':core.channel_to_json(r)},201)
  else:return json_error(400,'unknown command')
 elif body.startswith('//'):
  body=body[1:]
 if 'parent_id' in b and b['parent_id'] is not None and (not isinstance(b['parent_id'],int) or isinstance(b['parent_id'],bool)):return json_error(400,'invalid parent')
 ids=b.get('file_ids',[])
 if not isinstance(ids,list) or any(not isinstance(x,int) for x in ids):return json_error(400,'invalid files')
 c=core.connect()
 for fid in ids:
  f=c.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone()
  if not f:c.close();return json_error(400,'invalid file')
 c.close()
 try:
  r=core.create_message(u['id'],ch['id'],body,b.get('parent_id'))
  if ids:
   with core.tx() as c:
    c.executemany('UPDATE files SET message_id=? WHERE id=?',[(r['id'],fid) for fid in ids])
  return ok({'message':core.message_to_json(r)},201)
 except ValueError:return json_error(423,'archived')
 except ValueError:return json_error(423,'archived')


async def workspace_patch(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return json_error(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if role not in ('owner','admin'):return json_error(403,'forbidden')
 b=await read_json(req)
 if not b:return json_error(400,'invalid body')
 if 'name' in b and not isinstance(b['name'],str):return json_error(400,'invalid name')
 if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join mode')
 with core.tx() as c:
  if 'name' in b:c.execute('UPDATE workspaces SET name=? WHERE id=?',(b['name'],w['id']))
  if 'join_mode' in b:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(b['join_mode'],w['id']))
  w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
 return ok({'workspace':core.workspace_to_json(w)})
def inv_json(r):
 return {'code':r['code'],'workspace_id':r['workspace_id'],'invited_user_id':r['invited_user_id'],'expires_at':r['expires_at'],'max_uses':r['max_uses'],'uses':r['uses']}
async def invitations(req):
 u=await require_user(req);w=core.get_workspace(req.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if role not in ('owner','admin'):return json_error(403,'forbidden')
 if req.method=='GET':
  c=core.connect();rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)',(w['id'],time.time()));x=[inv_json(r) for r in rs];c.close();return ok({'invitations':x})
 b=await read_json(req)
 if b is None:return json_error(400,'invalid body')
 for k in ('expires_in_seconds','max_uses','invited_user_id','invited_username'):
  if k in b and k in ('expires_in_seconds','max_uses','invited_user_id') and not isinstance(b[k],int):return json_error(400,'invalid field')
 target=b.get('invited_user_id')
 if 'invited_username' in b:
  if not isinstance(b['invited_username'],str):return json_error(400,'invalid field')
  c=core.connect();r=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone();c.close();target=r['id'] if r else None
 code=secrets.token_urlsafe(12); exp=time.time()+b['expires_in_seconds'] if 'expires_in_seconds' in b else None; mx=1 if target else b.get('max_uses',1)
 if mx<1:return json_error(400,'invalid max uses')
 with core.tx() as c:c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,0)',(code,w['id'],target,exp,mx))
 return ok({'invitation':{'code':code,'workspace_id':w['id'],'invited_user_id':target,'expires_at':exp,'max_uses':mx,'uses':0}},201)
async def accept_inv(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 c=core.connect();r=c.execute('SELECT * FROM invitations WHERE code=?',(req.match_info['code'],)).fetchone();c.close()
 if not r or r['uses']>=r['max_uses'] or (r['expires_at'] and r['expires_at']<=time.time()):return json_error(404,'not found')
 if r['invited_user_id'] and r['invited_user_id']!=u['id']:return json_error(403,'targeted')
 with core.tx() as c:
  c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(r['code'],));core.add_workspace_member(c,r['workspace_id'],u['id'],'member');w=c.execute('SELECT * FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone()
 return ok({'workspace':core.workspace_to_json(w)})
async def revoke_inv(req):
 u=await require_user(req);w=core.get_workspace(req.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
 with core.tx() as c:
  r=c.execute('DELETE FROM invitations WHERE code=? AND workspace_id=?',(req.match_info['code'],w['id'],))
  if not r.rowcount:return json_error(404,'not found')
 return ok({'deleted':True})

async def role_patch(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return json_error(404,'not found')
 target=int(req.match_info['user_id'])
 b=await read_json(req)
 if not b or not isinstance(b.get('role'),str) or b['role'] not in ('admin','member','guest'): return json_error(400,'invalid role')
 caller=core.member_role(w['id'],u['id']); old=core.member_role(w['id'],target)
 if old is None:return json_error(404,'not found')
 if target==w['owner_id'] or caller not in ('owner','admin'):return json_error(403,'forbidden')
 if caller=='admin' and old in ('owner','admin'):return json_error(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',(b['role'],w['id'],target))
 return ok({'member':{'user_id':target,'role':b['role']}})
async def transfer(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return json_error(404,'not found')
 if u['id']!=w['owner_id']:return json_error(403,'forbidden')
 b=await read_json(req)
 if not b or not isinstance(b.get('new_owner_id'),int):return json_error(400,'invalid target')
 target=b['new_owner_id']
 if core.member_role(w['id'],target)!='admin':return json_error(400,'target must be admin')
 with core.tx() as c:
  c.execute('UPDATE workspace_members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id']))
  c.execute('UPDATE workspace_members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],target))
  c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
  r=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
 return ok({'workspace':core.workspace_to_json(r)})



async def dms(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 b=await read_json(req)
 if not b or not isinstance(b.get('user_id'),int):return json_error(400,'invalid user_id')
 other=b['user_id']; c=core.connect(); target=c.execute('SELECT * FROM users WHERE id=?',(other,)).fetchone()
 if not target:c.close();return json_error(404,'not found')
 a,bb=sorted((u['id'],other));r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bb)).fetchone();c.close()
 if r:return ok({'channel_id':r['channel_id']})
 with core.tx() as c:
  cur=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)'); ch=cur.lastrowid
  c.execute('INSERT INTO dms VALUES(?,?,?)',(a,bb,ch));core.add_channel_member(c,ch,a);core.add_channel_member(c,ch,bb)
 return ok({'channel_id':ch})
async def files(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 if req.method=='POST':
  try:
   reader=await req.multipart(); part=await reader.next()
   if not part or part.name!='file':return json_error(400,'missing file')
   data=await part.read(decode=False)
  except:return json_error(400,'invalid upload')
  if len(data)>10*1024*1024:return json_error(413,'too large')
  fn=part.filename or 'file'; ct=part.headers.get('Content-Type','application/octet-stream')
  with core.tx() as c:
   cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],fn,ct,len(data),core.now_iso())); fid=cur.lastrowid
  os.makedirs('/app/data/files',exist_ok=True);open('/app/data/files/'+str(fid),'wb').write(data)
  return ok({'file':{'id':fid,'uploader_id':u['id'],'filename':fn,'content_type':ct,'size':len(data),'created_at':core.now_iso()}},201)
 fid=int(req.match_info['id']);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
 if not r:return json_error(404,'not found')
 f={'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}
 if req.path.endswith('/download'):
  try:data=open('/app/data/files/'+str(fid),'rb').read()
  except:return json_error(404,'not found')
  return web.Response(body=data,content_type=r['content_type'],headers={'Content-Disposition':f'attachment; filename="{r["filename"]}"'})
 return ok({'file':f})




async def read_state_api(req):
 u=await require_user(req); ch=core.get_channel(int(req.match_info['id']))
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
 c=core.connect(); row=c.execute('SELECT * FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()
 last=row['last_read_event_id'] if row else 0
 if req.method=='POST':
  b=await read_json(req)
  if not b or not isinstance(b.get('last_read_event_id'),int) or isinstance(b.get('last_read_event_id'),bool) or b['last_read_event_id']<0:return json_error(400,'invalid read state')
  last=max(last,b['last_read_event_id'])
  with core.tx() as c:c.execute('INSERT INTO read_state(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],ch['id'],last))
 c=core.connect(); evs=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? AND event_id<=?',(ch['id'],last,core.head_event_id(ch['id']))).fetchall()
 unread=0;mentions=0
 for er in evs:
  x=json.loads(er['payload']); mid=x.get('message_id') or (x.get('message') or {}).get('id')
  if mid:
   m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
   if m and m['author_id']!=u['id']:
    unread+=1
    if u['id'] in core.mention_ids(mid,m['body'],ch['id'],m['author_id']): mentions+=1
 c.close()
 state={'channel_id':ch['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':mentions}
 return ok({'read_state':state})

async def user_api(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 if req.method=='GET':
  c=core.connect();r=c.execute('SELECT * FROM users WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
  return ok({'user':core.user_to_json(r)}) if r else json_error(404,'not found')
 b=await read_json(req)
 if not b:return json_error(400,'invalid body')
 allowed={'display_name':80,'status_text':140,'status_emoji':32,'avatar_url':512}
 for k,v in b.items():
  if k=='timezone': continue
  if k not in allowed or not isinstance(v,str) or not v or len(v)>allowed[k]:return json_error(400,'invalid profile')
 if 'timezone' in b:
  if not isinstance(b['timezone'],str) or b['timezone'] not in ('UTC','America/New_York','America/Los_Angeles','Europe/London','Asia/Tokyo'):return json_error(400,'invalid timezone')
 fields={k:v for k,v in b.items() if k in allowed or k=='timezone'}
 if not fields:return ok({'user':core.user_to_json(u)})
 with core.tx() as c:
  c.execute('UPDATE users SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',tuple(fields.values())+(u['id'],))
  r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
  core.emit_global(c,{'type':'user.updated','user':core.user_to_json(r)})
 return ok({'user':core.user_to_json(r)})

async def groups(req):
 u=await require_user(req); w=core.get_workspace(req.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if not role:return json_error(403,'forbidden')
 handle=req.match_info.get('handle')
 if not handle:
  parts=req.path.rstrip('/').split('/')
  if len(parts)>=2 and parts[-1] != 'groups': handle=parts[-1]
 c=core.connect()
 if req.method=='GET':
  rows=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)); out=[]
  for g in rows:
   ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]
   out.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids})
  c.close();return ok({'groups':out})
 g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone() if handle else None;c.close()
 if req.method=='DELETE':
  if not g:return ok({'deleted':True})
  if g['creator_id']!=u['id'] and u['id']!=w['owner_id']:return json_error(403,'forbidden')
  with core.tx() as c:c.execute('DELETE FROM groups WHERE id=?',(g['id'],))
  return ok({'deleted':True})
 b=await read_json(req)
 if req.method=='PATCH':
  if not g:return json_error(404,'not found')
  if g['creator_id']!=u['id'] and u['id']!=w['owner_id']:return json_error(403,'forbidden')
  if not b or ('user_ids' in b and not isinstance(b.get('user_ids'),list)):return json_error(400,'invalid users')
  c=core.connect()
  ids=[x['user_id'] for x in c.execute('SELECT user_id FROM workspace_members WHERE workspace_id=?',(w['id'],))]
  c.close()
  chosen=b.get('user_ids')
  if chosen is not None and (any(not isinstance(x,int) for x in chosen) or not set(chosen).issubset(set(ids))):return json_error(400,'invalid users')
  with core.tx() as c:
   if 'name' in b:
    if not isinstance(b['name'],str):return json_error(400,'invalid name')
    c.execute('UPDATE groups SET name=? WHERE id=?',(b['name'],g['id']))
   if chosen is not None:
    c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.executemany('INSERT INTO group_members VALUES(?,?)',[(g['id'],x) for x in chosen])
   g=c.execute('SELECT * FROM groups WHERE id=?',(g['id'],)).fetchone()
   members=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]
  return ok({'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':members}})
 if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9_-]+',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(b.get('user_ids',[]),list):return json_error(400,'invalid group')
 c=core.connect(); workspace_ids=[x['user_id'] for x in c.execute('SELECT user_id FROM workspace_members WHERE workspace_id=?',(w['id'],))];c.close()
 chosen=b['user_ids']
 if any(not isinstance(x,int) for x in chosen) or not set(chosen).issubset(set(workspace_ids)):return json_error(400,'invalid users')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],b['handle'],b['name'],u['id']));gid=cur.lastrowid;c.executemany('INSERT INTO group_members VALUES(?,?)',[(gid,x) for x in chosen])
  return ok({'group':{'id':gid,'handle':b['handle'],'name':b['name'],'member_user_ids':chosen}},201)
 except:return json_error(409,'group exists')

async def reactions(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(req.match_info['id']),)).fetchone()
 if not m:c.close();return json_error(404,'not found')
 ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone();c.close()
 if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
 emoji=req.match_info.get('emoji')
 if req.method=='POST':
  b=await read_json(req)
  if not b or not isinstance(b.get('emoji'),str) or not b['emoji']:return json_error(400,'invalid emoji')
  emoji=b['emoji']
  with core.tx() as c:
   cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(m['id'],u['id'],emoji))
   if cur.rowcount: core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
 else:
  if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
  with core.tx() as c:
   cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],emoji))
   if cur.rowcount: core.emit_event(c,m['channel_id'],'reaction.removed',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
 return ok({'reactions':core.reactions_for(m['id'])})

async def pin_api(req):
 u=await require_user(req); mid=int(req.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
 if not m:c.close();return json_error(404,'not found')
 ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone();c.close()
 if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
 if req.method=='GET': return json_error(404,'not found')
 if req.method=='DELETE':
  with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
  return ok({'unpinned':True})
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()));r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
 return ok({'pin':{'message_id':mid,'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']}})
async def pin_list(req):
 u=await require_user(req);ch=core.get_channel(int(req.match_info['id']))
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
 try:n,cur=core.parse_paging(req.query,'pins')
 except:return json_error(400,'paging')
 c=core.connect()
 if cur:
  rs=list(c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND p.message_id<? ORDER BY p.pinned_at DESC LIMIT ?',(ch['id'],cur,n+1)))
 else:
  rs=list(c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC LIMIT ?',(ch['id'],n+1)))
 rows=rs[:n];x=[{'message':core.message_to_json(r),'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']} for r in rows];c.close()
 return ok({'pins':x,'next_cursor':core.encode_cursor('pins',rows[-1]['message_id']) if len(rs)>n and rows else None})
async def search(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 q=req.query.get('q')
 if not q:return json_error(400,'missing q')
 workspace_filter=req.query.get('workspace_id')
 if workspace_filter is not None:
  try: workspace_filter=int(workspace_filter)
  except: return json_error(400,'invalid workspace_id')
 try:n,cur=core.parse_paging(req.query,'search')
 except:return json_error(400,'paging')
 c=core.connect();rs=[]
 for r in c.execute('SELECT m.*,ch.* FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.deleted=0 AND lower(m.body) LIKE ? ORDER BY m.id DESC',(f'%{q.lower()}%',)):
  chrow=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
  if (workspace_filter is None or chrow['workspace_id']==workspace_filter) and core.can_read_channel(u['id'],chrow): rs.append(r)
 rows=rs[cur:cur+n+1];more=len(rows)>n;rows=rows[:n];c.close()
 return ok({'results':[core.message_to_json(r) for r in rows],'next_cursor':core.encode_cursor('search',cur+n) if more else None})

async def protected_stub(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 return json_error(404,'not found')
async def replies(req):
 u=await require_user(req)
 if not u:return json_error(401,'unauthorized')
 try:n,cur=core.parse_paging(req.query,'replies')
 except:return json_error(400,'paging')
 c=core.connect(); parent=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone()
 if not parent:c.close();return json_error(404,'not found')
 ch=c.execute('SELECT * FROM channels WHERE id=?',(parent['channel_id'],)).fetchone();c.close()
 if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
 c=core.connect()
 if cur: rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id ASC LIMIT ?',(parent['id'],cur,n+1))
 else: rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id ASC LIMIT ?',(parent['id'],n+1))
 rows=list(rs);more=len(rows)>n;rows=rows[:n];x=[core.message_to_json(r) for r in rows];c.close()
 return ok({'replies':x,'next_cursor':core.encode_cursor('replies',rows[-1]['id']) if more and rows else None})
async def ws(req):
 u=core.user_for_token(req.query.get('token')) if req.query.get('token') else await require_user(req)
 if not u:return web.Response(status=401)
 w=web.WebSocketResponse();await w.prepare(req); conn_id=secrets.token_urlsafe(12)
 with core.tx() as c: c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],time.time()))
 subscribed={}
 c0=core.connect(); global_since=c0.execute('SELECT COALESCE(MAX(id),0) AS n FROM global_events').fetchone()['n']; c0.close()
 async def sender():
  nonlocal global_since
  while not w.closed:
   for gid,event in core.globals_after(global_since):
    global_since=gid; await w.send_json(event)
   for ch,last in list(subscribed.items()):
    for event in core.events_after(ch,last):
     subscribed[ch]=event['event_id']; await w.send_json(event)
   await asyncio.sleep(.05)
 task=asyncio.create_task(sender())
 try:
  async for msg in w:
   if msg.type!=1: continue
   try: b=json.loads(msg.data)
   except: continue
   ch=b.get('channel_id')
   if b.get('type')=='subscribe' and isinstance(ch,int):
    head=core.head_event_id(ch); subscribed[ch]=head
    await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
   elif b.get('type')=='resume' and isinstance(ch,int) and isinstance(b.get('since_event_id'),int):
    since=b['since_event_id']; subscribed[ch]=since
    for event in core.events_after(ch,since):
     subscribed[ch]=event['event_id']; await w.send_json(event)
    await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
 finally:
  task.cancel()
  with core.tx() as c: c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
 return w
async def root(req):
 try:return web.FileResponse('/app/static/index.html')
 except:return web.Response(text='<html><body><h1>Huddle</h1></body></html>',content_type='text/html')
async def appjs(req):
 try:return web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'})
 except:return web.Response(status=404)
app=web.Application()
app.router.add_get('/api/health',health);app.router.add_get('/app.js',appjs);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_route('*','/api/workspaces',workspaces);
app.router.add_route('PATCH','/api/workspaces/{slug}/members/{user_id}',role_patch)
app.router.add_route('POST','/api/workspaces/{slug}/transfer_ownership',transfer)
app.router.add_route('*','/api/workspaces/{slug}/invitations',invitations);app.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_inv)
app.router.add_post('/api/invitations/{code}/accept',accept_inv)
app.router.add_route('GET','/api/workspaces/{slug}',workspace);app.router.add_patch('/api/workspaces/{slug}',workspace_patch);app.router.add_get('/api/workspaces/{slug}/members',members);app.router.add_post('/api/workspaces/{slug}/channels',channels)
app.router.add_post('/api/channels/{id}/join',channel_join);app.router.add_patch('/api/messages/{id}',message_edit_delete);app.router.add_route('*','/api/channels/{id}/read',read_state_api);app.router.add_delete('/api/messages/{id}',message_edit_delete);app.router.add_route('*','/api/channels/{id}/messages',messages);app.router.add_get('/api/channels/{id}/pins',pin_list);app.router.add_route('*','/api/channels/{id}/{action}',channel_manage);app.router.add_patch('/api/channels/{id}',channel_manage);app.router.add_get('/api/messages/{id}/replies',replies)
app.router.add_get('/api/search',search);app.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',groups);app.router.add_route('*','/api/workspaces/{slug}/groups',groups);app.router.add_post('/api/dms',dms);app.router.add_post('/api/files',files);app.router.add_get('/api/files/{id}/download',files);app.router.add_get('/api/files/{id}',files);app.router.add_get('/api/users/{id}',user_api);app.router.add_patch('/api/users/me',user_api);app.router.add_post('/api/messages/{id}/reactions',reactions);app.router.add_delete('/api/messages/{id}/reactions/{emoji}',reactions)
app.router.add_get('/api/channels/{id}/pins',pin_list)
app.router.add_route('*','/api/messages/{id}/pin',pin_api)
app.router.add_get('/api/ws',ws);app.router.add_get('/',root)
web.run_app(app,host='127.0.0.1',port=8000+NODE)
