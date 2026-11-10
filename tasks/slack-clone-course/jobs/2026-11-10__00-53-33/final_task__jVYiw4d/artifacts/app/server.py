import aiohttp, asyncio, argparse, json, os, re, uuid, base64, time
from aiohttp import web, WSMsgType
import core
def j(data,status=200): return web.json_response(data,status=status)
def err(s,m): return j({'error':m},s)
async def body(r):
 try: x=await r.json(); return x if isinstance(x,dict) else None
 except: return None
def auth(r):
 h=r.headers.get('Authorization','')
 token=h[7:] if h.startswith('Bearer ') else r.query.get('token','')
 return core.user_for_token(token) if token else None
def uid(r):
 u=auth(r)
 if not u: raise web.HTTPUnauthorized()
 return u
def role(w,u): return core.member_role(w,u)
def member(c,u):
 return c and core.connect().execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(c['id'],u)).fetchone()
async def register(r):
 x=await body(r)
 if not x or not isinstance(x.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',x['username']) or not isinstance(x.get('password'),str) or len(x['password'])<8:return err(400,'invalid')
 with core.tx() as c:
  try:
   q=c.execute("INSERT INTO users(username,password,display_name) VALUES(?,?,?)",(x['username'],core.hash_password(x['password']),x.get('display_name') or x['username']))
   u=c.execute('SELECT * FROM users WHERE id=?',(q.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
  except: return err(409,'duplicate')
 return j({'user':core.user_to_json(u),'token':t},201)
async def login(r):
 x=await body(r); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(x.get('username') if x else '',)).fetchone(); c.close()
 if not u or not isinstance(x.get('password'),str) or not core.verify_password(x['password'],u['password']):return err(401,'invalid')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return j({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=auth(r)
 return j({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def health(r): return j({'status':'ok','node_id':r.app['node']})
async def workspaces(r):
 u=uid(r); c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close(); return j({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_ws(r):
 u=uid(r); x=await body(r)
 if not x or not re.fullmatch(r'[a-z0-9-]{2,32}',str(x.get('slug',''))):return err(400,'invalid')
 with core.tx() as c:
  try:
   q=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x.get('name',''),u['id'])); w=q.lastrowid
   q=c.execute("INSERT INTO channels(workspace_id,name) VALUES(?, 'general')",(w,)); ch=q.lastrowid
   core.add_workspace_member(c,w,u['id'],'owner'); cr=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone(); wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone()
  except: return err(409,'duplicate')
 return j({'workspace':dict(wr),'general_channel':dict(cr)},201)
async def ws_detail(r):
 u=uid(r); w=core.get_workspace(r.path.strip('/').split('/')[2])
 if not w:return err(404,'not found')
 c=core.connect(); chans=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
 return j({'workspace':dict(w),'channels':[dict(x) for x in chans],'read_state':{}})
async def channels(r):
 u=uid(r); parts=r.path.strip('/').split('/')
 slug=parts[2] if len(parts)>2 else ''
 w=core.get_workspace(slug)
 if not w:return err(404,'not found')
 x=await body(r)
 if not x or not isinstance(x.get('name'),str):return err(400,'invalid')
 with core.tx() as c:
  try:
   q=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')))
   c.execute('INSERT OR IGNORE INTO channel_members(channel_id,user_id) VALUES(?,?)',(q.lastrowid,u['id']))
   created=dict(c.execute('SELECT * FROM channels WHERE id=?',(q.lastrowid,)).fetchone())
  except:
   return err(409,'duplicate')
 return j({'channel':created},201)
async def join(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 try: cid=int(r.path.split('/')[3])
 except:return err(404,'not found')
 ch=core.get_channel(cid)
 if not ch:return err(404,'not found')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
  if ch['workspace_id'] is not None: core.add_workspace_member(c,ch['workspace_id'],u['id'])
  fresh=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
 return j({'channel':dict(fresh)})
async def messages(r):
 u=uid(r); ch=core.get_channel(int(r.path.split('/')[3]))
 if not ch:return err(404,'not found')
 c=core.connect()
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():
  c.close(); return err(403,'forbidden')
 rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC LIMIT 200',(ch['id'],)).fetchall()
 out=[]
 for x in rows:
  item=core.msg_json(c,x)
  rr=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(x['id'],)).fetchall(); grouped={}
  for z in rr: grouped.setdefault(z['emoji'],[]).append(z['user_id'])
  item['reactions']=[{'emoji':e,'count':len(v),'user_ids':v} for e,v in grouped.items()]
  out.append(item)
 c.close(); return j({'messages':out,'next_cursor':None})
async def post(r):
 u=uid(r); ch=core.get_channel(int(r.path.split('/')[3])); x=await body(r)
 if not ch:return err(404,'not found')
 c=core.connect()
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():
  c.close(); return err(403,'forbidden')
 if not x or not isinstance(x.get('body'),str) or not x['body'].strip():return err(400,'empty')
 if ch['is_archived'] and x['body'] != '/unarchive': return err(423,'archived')
 fids=x.get('file_ids',[])
 if fids is not None and (not isinstance(fids,list) or any(not isinstance(v,int) for v in fids)):
  c.close(); return err(400,'invalid files')
 for fid in fids:
  z=c.execute('SELECT uploader_id,message_id FROM files WHERE id=?',(fid,)).fetchone()
  if not z or z['uploader_id']!=u['id'] or z['message_id'] is not None:
   c.close(); return err(400,'invalid file')
 c.close()
 body_text=x['body']
 if body_text.startswith('//'):
  body_text=body_text[1:]
 elif body_text.startswith('/me'):
  if body_text == '/me' or not body_text.startswith('/me ') or not body_text[4:].strip(): return err(400,'invalid command')
 elif body_text.startswith('/shrug'):
  tail=body_text[6:].strip()
  body_text=(tail+' ' if tail else '')+'¯\\_(ツ)_/¯'
 elif body_text.startswith('/topic'):
  tail=body_text[6:].strip()
  if not tail:return err(400,'invalid command')
  with core.tx() as c:
   c.execute('UPDATE channels SET topic=? WHERE id=?',(tail,ch['id']))
   updated=dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())
   updated['is_archived']=bool(updated['is_archived'])
  return j({'message':None,'channel':updated},201)
 elif body_text.startswith('/invite'):
  tail=body_text[7:].strip()
  if not tail.startswith('@') or len(tail)<2:return err(400,'invalid command')
  c=core.connect(); target=c.execute('SELECT * FROM users WHERE username=?',(tail[1:],)).fetchone()
  if not target:c.close(); return err(404,'not found')
  if ch['workspace_id'] is None:c.close(); return err(400,'invalid command')
  if not c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],target['id'])).fetchone():
   c.close(); return err(403,'forbidden')
  c.close()
  with core.tx() as c:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],target['id']))
  return j({'message':None,'invited_user':core.user_to_json(target)},201)
 elif body_text in ('/archive','/unarchive'):
  if ch['workspace_id'] is None or core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
  val=1 if body_text=='/archive' else 0
  with core.tx() as c:
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
   updated=dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())
   updated['is_archived']=bool(updated['is_archived'])
  return j({'message':None,'channel':updated},201)
 elif body_text.startswith('/'):
  return err(400,'unknown command')
 msg=core.create_message(u['id'],ch['id'],body_text,x.get('parent_id'))
 if fids:
  with core.tx() as c:
   for fid in fids:c.execute('UPDATE files SET message_id=? WHERE id=?',(msg['id'],fid))
  c=core.connect(); row=c.execute('SELECT * FROM messages WHERE id=?',(msg['id'],)).fetchone(); msg=core.msg_json(c,row); c.close()
 return j({'message':msg},201)
async def groups(r):
 u=uid(r); slug=r.path.strip('/').split('/')[2]; w=core.get_workspace(slug)
 if not w:return err(404,'not found')
 c=core.connect()
 if not c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone():c.close();return err(403,'forbidden')
 parts=r.path.strip('/').split('/'); handle=parts[4] if len(parts)>4 else None
 if r.method=='GET':
  rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall(); out=[core.group_json(c,x) for x in rows]; c.close(); return j({'groups':out})
 if r.method=='POST':
  x=await body(r); h=x.get('handle') if x else None; ids=x.get('user_ids',[]) if x else []
  if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',h) or not isinstance(ids,list):c.close();return err(400,'invalid')
  for z in ids:
   if not isinstance(z,int) or not c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],z)).fetchone():c.close();return err(400,'invalid member')
  if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone():c.close();return err(409,'duplicate')
  with core.tx() as t:
   q=t.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],h,x.get('name',''),u['id'])); gid=q.lastrowid
   for z in ids:t.execute('INSERT INTO group_members VALUES(?,?)',(gid,z))
   g=t.execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone(); out=core.group_json(t,g)
  return j({'group':out},201)
 if not handle:return err(404,'not found')
 g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone()
 if not g:
  c.close()
  return j({'deleted':True}) if r.method=='DELETE' else err(404,'not found')
 if r.method=='DELETE':
  c.close()
  if u['id'] not in (g['creator_id'],w['owner_id']):return err(403,'forbidden')
  with core.tx() as t:t.execute('DELETE FROM groups_ WHERE id=?',(g['id'],))
  return j({'deleted':True})
 x=await body(r)
 if u['id'] not in (g['creator_id'],w['owner_id']):return err(403,'forbidden')
 with core.tx() as t:
  if 'name' in x:t.execute('UPDATE groups_ SET name=? WHERE id=?',(x['name'],g['id']))
  if 'user_ids' in x:
   t.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
   for z in x['user_ids']:t.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],z))
  g=t.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone()
  out=core.group_json(t,g)
 return j({'group':out})

async def channel_members(r):
 u=uid(r); cid=int(r.path.split('/')[3]); ch=core.get_channel(cid)
 if not ch:return err(404,'not found')
 c=core.connect()
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():
  c.close(); return err(403,'forbidden')
 rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,)).fetchall(); c.close()
 return j({'members':[core.user_to_json(x) for x in rows]})
async def reactions(r):
 u=auth(r)
 if not u: return err(401,'unauthorized')
 try: mid=int(r.path.split('/')[3])
 except: return err(404,'not found')
 x=await body(r)
 emoji=x.get('emoji') if x else None
 if r.method=='POST' and (not isinstance(emoji,str) or not emoji): return err(400,'invalid emoji')
 c0=core.connect(); m=c0.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
 if not m: c0.close(); return err(404,'not found')
 ch=c0.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
 allowed=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
 c0.close()
 if not allowed: return err(403,'forbidden')
 if r.method=='DELETE': emoji=r.path.rsplit('/',1)[-1]
 with core.tx() as c:
  if r.method=='POST':
   q=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
   changed=q.rowcount>0
  else:
   q=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
   changed=q.rowcount>0
  if changed:
   kind='reaction.added' if r.method=='POST' else 'reaction.removed'
   core.emit_event(c,m['channel_id'],kind,{'message_id':mid,'emoji':emoji,'user_id':u['id']})
  rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall(); d={}
  for z in rows:d.setdefault(z['emoji'],[]).append(z['user_id'])
 return j({'reactions':[{'emoji':k,'count':len(v),'user_ids':v} for k,v in d.items()]})
async def profile(r):
 u=uid(r); c=core.connect(); parts=r.path.strip('/').split('/'); ident=parts[-1]; target=u if ident=='me' else c.execute('SELECT * FROM users WHERE id=?',(int(ident),)).fetchone(); c.close()
 if not target:return err(404,'not found')
 if r.method=='GET':return j({'user':core.user_to_json(target)})
 x=await body(r)
 if not x:return err(400,'invalid')
 allowed=['display_name','timezone','avatar_url','status_text','status_emoji']
 if any(k not in allowed or not isinstance(v,str) for k,v in x.items()):return err(400,'invalid')
 limits={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512)}
 for k,v in x.items():
  if k=='timezone':
   try: __import__('zoneinfo').ZoneInfo(v)
   except: return err(400,'invalid timezone')
  elif not limits[k][0] <= len(v) <= limits[k][1]: return err(400,'invalid length')
 with core.tx() as c:
  for k,v in x.items(): c.execute(f'UPDATE users SET {k}=? WHERE id=?',(v,u['id']))
  z=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
  core.emit_global(c,{'type':'user.updated','user':core.user_to_json(z)})
 return j({'user':core.user_to_json(z)})
async def ws_handler(r):
 u=auth(r)
 if not u: return err(401,'unauthorized')
 ws=web.WebSocketResponse(); await ws.prepare(r); subscribed={}
 gc=core.connect(); global_last=gc.execute('SELECT COALESCE(MAX(id),0) n FROM global_events').fetchone()['n']; gc.close()
 conn_id=str(uuid.uuid4())
 core.touch_presence(conn_id,u['id']) if hasattr(core,'touch_presence') else None
 async def poll():
  nonlocal global_last
  while not ws.closed:
   await asyncio.sleep(.05)
   c=core.connect()
   globals_=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_last,)).fetchall()
   for gz in globals_:
    await ws.send_json(json.loads(gz['payload'])); global_last=gz['id']
   for ch,last in list(subscribed.items()):
    rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)).fetchall()
    for z in rows:
     frame=json.loads(z['payload']); subscribed[ch]=frame['event_id']; await ws.send_json(frame)
   c.close()
 try:
  task=asyncio.create_task(poll())
  async for msg in ws:
   if msg.type==WSMsgType.TEXT:
    x=json.loads(msg.data)
    if x.get('type') in ('subscribe','resume'):
     ch=int(x['channel_id']); c=core.connect(); head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close()
     hv=head['last_event_id'] if head else 0
     subscribed[ch]=(hv if x['type']=='subscribe' else int(x.get('since_event_id',0)))
     await ws.send_json({'type':'subscribed' if x['type']=='subscribe' else 'resumed','channel_id':ch,'head_event_id':hv})
 finally:
  task.cancel()
  if hasattr(core,'drop_presence'): core.drop_presence(conn_id)
 return ws


async def dms(r):
 u=auth(r); x=await body(r)
 if not x or not isinstance(x.get('user_id'),int): return err(400,'invalid user_id')
 other=x['user_id']
 if other==u['id']: return err(400,'invalid user_id')
 c=core.connect(); target=c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone()
 if not target: c.close(); return err(404,'not found')
 a,b=sorted((u['id'],other)); z=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone(); c.close()
 if z:return j({'channel_id':z['channel_id']})
 with core.tx() as c:
  q=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)')
  cid=q.lastrowid; c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid))
  c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a)); c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,b))
 return j({'channel_id':cid})
async def upload(r):
 u=auth(r)
 try:
  rd=await r.multipart(); part=await rd.next()
  if not part or part.name!='file': return err(400,'file required')
  data=await part.read()
  if len(data)>10*1024*1024:return err(413,'too large')
  fn=part.filename or 'file'; ct=part.headers.get('Content-Type','application/octet-stream')
 except:return err(400,'invalid upload')
 with core.tx() as c:
  q=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],fn,ct,len(data),core.now_iso()))
  fid=q.lastrowid
  os.makedirs('/app/data/files',exist_ok=True); open('/app/data/files/'+str(fid),'wb').write(data)
  row=dict(c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone())
 return j({'file':row},201)
async def file_get(r):
 u=auth(r); parts=r.path.split('/'); fid=int(parts[3]) if len(parts)>3 else 0
 c=core.connect(); row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone(); c.close()
 if not row:return err(404,'not found')
 if len(parts)>4 and parts[4]=='download':
  if not os.path.exists('/app/data/files/'+str(fid)):return err(404,'not found')
  return web.Response(body=open('/app/data/files/'+str(fid),'rb').read(),content_type=row['content_type'],headers={'Content-Disposition':'attachment; filename="'+row['filename']+'"'})
 return j({'file':dict(row)})

async def handler(r):
 try:
  p=r.path
  if p=='/api/ws': return await ws_handler(r)
  if p.startswith('/api/') and p not in ('/api/auth/register','/api/auth/login','/api/auth/me','/api/health') and not auth(r):
   return err(401,'unauthorized')
  if p=='/api/auth/register':return await register(r)
  if p=='/api/auth/login':return await login(r)
  if p=='/api/auth/me':return await me(r)
  if p=='/api/health':return await health(r)
  if p.startswith('/api/workspaces/') and '/groups' in p:return await groups(r)
  if p=='/api/dms' and r.method=='POST':return await dms(r)
  if p=='/api/files' and r.method=='POST':return await upload(r)
  if p.startswith('/api/files/') and r.method=='GET':return await file_get(r)
  if p=='/api/workspaces' and r.method=='GET':return await workspaces(r)
  if p=='/api/workspaces' and r.method=='POST':return await create_ws(r)
  if p.startswith('/api/workspaces/') and p.count('/')==3 and r.method=='GET':return await ws_detail(r)
  if p.endswith('/channels') and r.method=='POST':return await channels(r)
  if re.fullmatch(r'/api/channels/\d+/members',p):return await channel_members(r)
  if re.fullmatch(r'/api/channels/\d+/join',p):return await join(r)
  if re.fullmatch(r'/api/channels/\d+/messages',p):return await (messages(r) if r.method=='GET' else post(r))
  if re.fullmatch(r'/api/messages/\d+/reactions',p) or re.fullmatch(r'/api/messages/\d+/reactions/[^/]+',p):return await reactions(r)
  if p=='/api/users/me' or re.fullmatch(r'/api/users/\d+',p):return await profile(r)
  return err(404,'not found')
 except web.HTTPException: return err(401,'unauthorized')
 except Exception as e:return err(500,str(e))
async def main():
 ap=argparse.ArgumentParser();ap.add_argument('--node',type=int,default=0); a=ap.parse_args()
 app=web.Application();app['node']=a.node;app.router.add_route('*','/{tail:.*}',handler)
 runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',8000+a.node).start();await asyncio.Event().wait()
if __name__=='__main__':asyncio.run(main())
