import asyncio, argparse, json, os, re, uuid, base64, mimetypes
from aiohttp import web, WSMsgType
import core
def json_error(s,m): return web.json_response({'error':m},status=s)
async def read_json(r):
 try: return await r.json()
 except: return None
def require_user(r):
 h=r.headers.get('Authorization','')
 if not h.startswith('Bearer '): return None
 return core.user_for_token(h[7:])
def out(x,status=200): return web.json_response(x,status=status)
def uid(r):
 u=require_user(r)
 if not u and r.query.get('token'): u=core.user_for_token(r.query['token'])
 return u
async def auth_register(r):
 b=await read_json(r)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid registration')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
   u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
  return out({'user':core.user_to_json(u),'token':t},201)
 except: return json_error(409,'username exists')
async def auth_login(r):
 b=await read_json(r); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username'),)).fetchone() if isinstance(b,dict) else None;c.close()
 if not u or not isinstance(b.get('password'),str) or not core.verify_password(b['password'],u['password']): return json_error(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=uid(r)
 return out({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def workspaces(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 b=await read_json(r)
 if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))) or not isinstance(b.get('name'),str):return json_error(400,'invalid workspace')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']));w=cur.lastrowid;core.add_workspace_member(c,w,u['id'],'owner'); ch=core.create_channel_row(c,w,'general');core.add_channel_member(c,ch,u['id']); wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone(); cr=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  return out({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(cr)},201)
 except:return json_error(409,'duplicate')
async def workspace(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return json_error(404,'not found')
 c=core.connect(); cs=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in cs]})
async def create_channel(r):
 u=uid(r); w=core.get_workspace(r.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin','member'):return json_error(403,'forbidden')
 b=await read_json(r)
 if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{1,32}',str(b.get('name',''))):return json_error(400,'invalid channel')
 try:
  with core.tx() as c:
   i=core.create_channel_row(c,w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''));core.add_channel_member(c,i,u['id']); x=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
  return out({'channel':core.channel_to_json(x)},201)
 except:return json_error(409,'duplicate')
async def join(r):
 u=uid(r); ch=core.get_channel(r.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 with core.tx() as c:
  if ch['workspace_id']:core.add_workspace_member(c,ch['workspace_id'],u['id']);core.add_channel_member(c,ch['id'],u['id'])
 return out({'channel':core.channel_to_json(ch)})
async def messages(r):
 u=uid(r); ch=core.get_channel(r.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 c=core.connect(); mem=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
 if not mem:return json_error(403,'forbidden')
 rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(ch['id'],)).fetchall();c.close()
 return out({'messages':[core.message_to_json(x) for x in rows],'next_cursor':None})

async def replies(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 try: mid=int(r.match_info['id'])
 except:return json_error(404,'not found')
 c=core.connect(); parent=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
 if not parent:c.close();return json_error(404,'not found')
 mem=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(parent['channel_id'],u['id'])).fetchone()
 rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,)).fetchall();c.close()
 if not mem:return json_error(403,'forbidden')
 return out({'replies':[core.message_to_json(x) for x in rows],'next_cursor':None})


async def channel_patch(r):
 u=uid(r); ch=core.get_channel(r.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 c=core.connect();mem=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone();c.close()
 if not mem:return json_error(403,'forbidden')
 b=await read_json(r)
 if not isinstance(b,dict) or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return json_error(400,'invalid topic')
 with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']))
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})

async def channel_members(r):
 u=uid(r); ch=core.get_channel(r.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
 return out({'members':[core.user_to_json(x) for x in rows]})

async def post(r):
 u=uid(r); ch=core.get_channel(r.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 c0=core.connect(); mem=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
 if not mem:return json_error(403,'forbidden')
 b=await read_json(r)
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty body')
 body=b['body']
 if ch['is_archived'] and body != '/unarchive':return json_error(423,'archived')
 if body.startswith('/'):
  if body.startswith('//'): body=body[1:]
  elif body=='/me' or (body.startswith('/me ') and not body[4:].strip()): return json_error(400,'missing text')
  elif body.startswith('/me '): body=body
  elif body=='/shrug': body='¯\\_(ツ)_/¯'
  elif body.startswith('/shrug '): body=body[7:]+' ¯\\_(ツ)_/¯'
  elif body.startswith('/topic '):
   topic=body[7:]
   if not topic:return json_error(400,'topic')
   with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']))
   x=core.get_channel(ch['id']);return out({'message':None,'channel':core.channel_to_json(x)},201)
  elif body.startswith('/invite @'):
   name=body[9:].strip(); cc=core.connect(); target=cc.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone();cc.close()
   if not target:return json_error(404,'user')
   with core.tx() as c:
    core.add_channel_member(c,ch['id'],target['id'])
    c.execute('INSERT OR IGNORE INTO memberships(workspace_id,user_id,role) VALUES(?,?,?)',(ch['workspace_id'],target['id'],'member'))
   return out({'message':None,'invited_user':core.user_to_json(target)},201)
  elif body=='/archive' or body=='/unarchive':
   if not ch['workspace_id']:return json_error(403,'forbidden')
   cc=core.connect()
   owner_id=cc.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
   cc.close()
   if not owner_id or int(owner_id['owner_id']) != int(u['id']):return json_error(403,'forbidden')
   val=1 if body=='/archive' else 0
   with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
   x=core.get_channel(ch['id']);return out({'message':None,'channel':core.channel_to_json(x)},201)
  else:return json_error(400,'unknown command')
 b['body']=body
 file_ids=b.get('file_ids',[])
 if file_ids is None:file_ids=[]
 if not isinstance(file_ids,list) or any(not isinstance(x,int) for x in file_ids):return json_error(400,'invalid files')
 with core.tx() as c:
  for fid in file_ids:
   f=c.execute('SELECT id FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone()
   if not f:return json_error(400,'invalid file')
  cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],b['body'],b.get('parent_id'),core.now_iso()));i=cur.lastrowid
  for fid in file_ids:c.execute('UPDATE files SET message_id=? WHERE id=?',(i,fid))
  x=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone(); mentions=core.resolve_mentions(c,body,ch,u['id']); xdict=core.message_to_json(x); xdict['mentions']=mentions; f=core.emit_event(c,ch['id'],'message.reply' if b.get('parent_id') else 'message.created',{'message':xdict})
 xdict=core.message_to_json(x); xdict['mentions']=mentions
 return out({'message':xdict},201)

async def message_edit(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 try: mid=int(r.match_info['id'])
 except:return json_error(404,'not found')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return json_error(404,'not found')
 if m['deleted']:return json_error(409,'deleted')
 if m['author_id']!=u['id']:return json_error(403,'forbidden')
 b=await read_json(r)
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'body')
 ch=core.get_channel(m['channel_id'])
 with core.tx() as c:
  c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],core.now_iso(),mid))
  x=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  z=core.message_to_json(x);z['mentions']=core.resolve_mentions(c,b['body'],ch,u['id'])
  core.emit_event(c,ch['id'],'message.updated',{'message':z})
 return out({'message':z})
async def message_delete(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 try:mid=int(r.match_info['id'])
 except:return json_error(404,'not found')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return json_error(404,'not found')
 if m['deleted']:return out({'deleted':True})
 ch=core.get_channel(m['channel_id'])
 cc=core.connect();owner=cc.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch['workspace_id'] else None;cc.close()
 if m['author_id']!=u['id'] and (not owner or owner['owner_id']!=u['id']):return json_error(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
  core.emit_event(c,ch['id'],'message.deleted',{'message_id':mid})
 return out({'deleted':True})

async def react(r):
 u=uid(r); 
 if not u:return json_error(401,'unauthorized')
 mid=int(r.match_info['id']); b=await read_json(r); emoji=b.get('emoji') if isinstance(b,dict) else ''
 if not emoji:return json_error(400,'emoji')
 with core.tx() as c:c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
 c=core.connect(); rows=c.execute('SELECT emoji,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall();c.close()
 return out({'reactions':[{'emoji':x['emoji'],'count':x['n']} for x in rows]})

def reaction_list(mid):
 c=core.connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall(); c.close()
 d={}
 for x in rows: d.setdefault(x['emoji'],[]).append(x['user_id'])
 return [{'emoji':k,'count':len(v),'user_ids':v} for k,v in d.items()]
async def reaction_delete(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 try: mid=int(r.match_info['id'])
 except:return json_error(404,'not found')
 c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
 if not m:return json_error(404,'not found')
 with core.tx() as c:
  cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],r.match_info['emoji']))
  if cur.rowcount:
   core.emit_event(c,m['channel_id'],'reaction.removed',{'message_id':mid,'emoji':r.match_info['emoji'],'user_id':u['id']})
 return out({'reactions':reaction_list(mid)})
async def reaction_post(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 try: mid=int(r.match_info['id'])
 except:return json_error(404,'not found')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return json_error(404,'not found')
 cc=core.connect(); mem=cc.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone(); cc.close()
 if not mem:return json_error(403,'forbidden')
 b=await read_json(r); emoji=b.get('emoji') if isinstance(b,dict) else None
 if not isinstance(emoji,str) or not emoji:return json_error(400,'emoji')
 with core.tx() as c:
  exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
  if not exists:
   c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
   core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
 return out({'reactions':reaction_list(mid)})
async def user_get(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM users WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 return out({'user':core.user_to_json(x)}) if x else json_error(404,'not found')
async def dm(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 b=await read_json(r); oid=b.get('user_id') if isinstance(b,dict) else None
 if not isinstance(oid,int):return json_error(400,'user_id')
 c=core.connect();x=c.execute('SELECT id FROM users WHERE id=?',(oid,)).fetchone();c.close()
 if not x:return json_error(404,'not found')
 a,bid=sorted((u['id'],oid))
 with core.tx() as c:
  x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bid)).fetchone()
  if x:return out({'channel_id':x[0]})
  cur=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)'); ch=cur.lastrowid
  c.execute('INSERT INTO dms VALUES(?,?,?)',(a,bid,ch));core.add_channel_member(c,ch,a);core.add_channel_member(c,ch,bid)
 return out({'channel_id':ch})
async def file_download(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not x:return json_error(404,'not found')
 try:data=open('/app/data/files/'+str(x['id']),'rb').read()
 except:return json_error(404,'not found')
 return web.Response(body=data,content_type=x['content_type'],headers={'Content-Disposition':'attachment; filename="'+x['filename']+'"'})
async def file_get(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 return out({'file':dict(x)}) if x else json_error(404,'not found')


async def file_upload(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 try:
  rd=await r.multipart()
  part=await rd.next()
  if not part or part.name!='file': return json_error(400,'invalid upload')
  data=await part.read()
 except: return json_error(400,'invalid upload')
 if len(data)>10485760:return json_error(413,'file too large')
 name=part.filename or 'file'; ctype=part.headers.get('Content-Type','application/octet-stream')
 with core.tx() as c:
  cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],name,ctype,len(data),core.now_iso()))
  fid=cur.lastrowid
  os.makedirs('/app/data/files',exist_ok=True)
  open('/app/data/files/'+str(fid),'wb').write(data)
  row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
 return out({'file':dict(row)},201)
def group_json(c,g):
 ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id',(g['id'],))]
 return {'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids}
async def groups(r):
 u=uid(r); w=core.get_workspace(r.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall();outx=[group_json(c,x) for x in rows];c.close()
 return out({'groups':outx})
async def group_create(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
 b=await read_json(r)
 if not isinstance(b,dict) or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(b.get('user_ids'),list):return json_error(400,'invalid group')
 c=core.connect()
 if c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(w['id'],b['handle'])).fetchone():c.close();return json_error(409,'duplicate')
 for x in b['user_ids']:
  if not isinstance(x,int) or not c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone():c.close();return json_error(400,'invalid member')
 c.close()
 with core.tx() as c:
  cur=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],b['handle'],b['name'],u['id']));gid=cur.lastrowid
  for x in b['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,x))
  g=c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone()
  return out({'group':group_json(c,g)},201)
async def group_update(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 c=core.connect();g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone()
 if not g:c.close();return json_error(404,'not found')
 if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:c.close();return json_error(403,'forbidden')
 b=await read_json(r)
 with core.tx() as c:
  if isinstance(b,dict) and isinstance(b.get('name'),str):c.execute('UPDATE groups SET name=? WHERE id=?',(b['name'],g['id']))
  if isinstance(b,dict) and isinstance(b.get('user_ids'),list):
   c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
   for x in b['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],x))
  g=c.execute('SELECT * FROM groups WHERE id=?',(g['id'],)).fetchone()
  return out({'group':group_json(c,g)})
async def group_delete(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return json_error(401,'unauthorized')
 if not w:return json_error(404,'not found')
 c=core.connect();g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone();c.close()
 if not g:return out({'deleted':True})
 if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return json_error(403,'forbidden')
 with core.tx() as c:c.execute('DELETE FROM groups WHERE id=?',(g['id'],))
 return out({'deleted':True})
async def health(r): return out({'status':'ok','node_id':r.app['node']})
async def ws(r):
 u=uid(r)
 if not u:return web.Response(status=401)
 w=web.WebSocketResponse();await w.prepare(r); subs={}; alive=True; global_seen=0
 conn_id=str(uuid.uuid4())
 core.touch_presence(conn_id,u['id']) if hasattr(core,'touch_presence') else None
 async def sender():
  nonlocal global_seen
  while alive:
   if hasattr(core,'touch_presence'): core.touch_presence(conn_id,u['id'])
   for _,frame in core.globals_after(global_seen): await w.send_json(frame)
   gc=core.connect()
   global_seen=gc.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]
   gc.close()
   for ch,last in list(subs.items()):
    for e in core.events_after(ch,last): await w.send_json(e);subs[ch]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(sender())
 try:
  async for m in w:
   if m.type==WSMsgType.TEXT:
    core.touch_presence(conn_id,u['id'])
    b=json.loads(m.data)
    if b.get('type') in ('subscribe','resume'):
     ch=int(b['channel_id']); head=core.head_event_id(ch)
     if b['type']=='resume':
      since=int(b.get('since_event_id',0))
      for frame in core.events_after(ch,since):
       await w.send_json(frame)
      subs[ch]=head
      await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':head})
     else:
      subs[ch]=head
      await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
 finally:
  alive=False;task.cancel()
  if hasattr(core,'drop_presence'): core.drop_presence(conn_id)
 return w
async def profile_patch(r):
 u=uid(r)
 if not u:return json_error(401,'unauthorized')
 b=await read_json(r)
 try:x=core.update_profile(u,b)
 except:return json_error(400,'invalid profile')
 return out({'user':core.user_to_json(x)})
app=web.Application()
app.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'));app.router.add_get('/app.js',lambda r:web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'}))
app.router.add_get('/api/health',health);app.router.add_post('/api/auth/register',auth_register);app.router.add_post('/api/auth/login',auth_login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_workspace);app.router.add_get('/api/workspaces/{slug}',workspace);app.router.add_post('/api/workspaces/{slug}/channels',create_channel)
app.router.add_post('/api/channels/{id}/join',join);app.router.add_patch('/api/channels/{id}',channel_patch);app.router.add_get('/api/channels/{id}/members',channel_members);app.router.add_get('/api/channels/{id}/messages',messages);app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_post('/api/channels/{id}/messages',post);app.router.add_patch('/api/messages/{id}',message_edit);app.router.add_delete('/api/messages/{id}',message_delete);app.router.add_post('/api/messages/{id}/reactions',reaction_post);app.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction_delete);app.router.add_post('/api/dms',dm);app.router.add_post('/api/files',file_upload);app.router.add_get('/api/files/{id}',file_get);app.router.add_get('/api/files/{id}/download',file_download);app.router.add_get('/api/users/{id}',user_get);
app.router.add_patch('/api/users/me',profile_patch);app.router.add_get('/api/workspaces/{slug}/groups',groups);app.router.add_post('/api/workspaces/{slug}/groups',group_create);app.router.add_patch('/api/workspaces/{slug}/groups/{handle}',group_update);app.router.add_delete('/api/workspaces/{slug}/groups/{handle}',group_delete);app.router.add_get('/api/ws',ws)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);a=p.parse_args();app['node']=a.node;web.run_app(app,host='127.0.0.1',port=8000+a.node)
