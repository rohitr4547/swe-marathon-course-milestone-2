import sys,json,re,asyncio,os,urllib.parse,secrets
from aiohttp import web,WSMsgType
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
PRESENT=set()
def resp(x,status=200): return web.json_response(x,status=status)
def err(s,m): return resp({'error':m},s)
async def body(r):
 try:
  x=await r.json(); return x if isinstance(x,dict) else None
 except: return None
def auth(r):
 h=r.headers.get('Authorization',''); return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
def access(c,ch,uid):
 return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,uid)).fetchone() is not None
def reactions(c,mid):
 rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall(); out=[]
 for emoji in dict.fromkeys(x['emoji'] for x in rows):
  ids=[x['user_id'] for x in rows if x['emoji']==emoji];out.append({'emoji':emoji,'count':len(ids),'user_ids':ids})
 return out
async def api(r):
 if r.path=='/api/health': return resp({'status':'ok','node_id':NODE})
 u=auth(r)
 if r.path=='/api/auth/register' and r.method=='POST':
  x=await body(r)
  if not x or not isinstance(x.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',x['username']) or not isinstance(x.get('password'),str) or len(x['password'])<8:return err(400,'invalid registration')
  try:
   with core.tx() as c:
    cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(x['username'],core.hash_password(x['password']),x.get('display_name') or x['username'])); uid=cur.lastrowid;t=core.issue_token(c,uid);z=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
   return resp({'user':core.user_json(z),'token':t},201)
  except: return err(409,'duplicate username')
 if r.path=='/api/auth/login' and r.method=='POST':
  x=await body(r) or {};c=core.connect();z=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),core.hash_password(x.get('password','')))).fetchone()
  if not z:c.close();return err(401,'invalid credentials')
  t=core.issue_token(c,z['id']);c.close();return resp({'user':core.user_json(z),'token':t})
 if not u:return err(401,'unauthorized')
 if r.path=='/api/auth/me': return resp({'user':core.user_json(u)})
 x={} if (r.path=='/api/files' and r.method=='POST') else (await body(r) if r.method in ('POST','PATCH','DELETE') else {})
 if re.match(r'/api/users/\d+$',r.path) and r.method=='GET':
  z=core.connect().execute('SELECT * FROM users WHERE id=?',(int(r.path.rsplit('/',1)[1]),)).fetchone()
  if not z:return err(404,'user not found')
  return resp({'user':core.user_json(z)})
 if r.path=='/api/users/me' and r.method=='PATCH':
  allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
  if not isinstance(x,dict) or any(k not in allowed for k in x):return err(400,'invalid field')
  if 'timezone' in x:
   try:
    from zoneinfo import ZoneInfo
    if not isinstance(x['timezone'],str): raise ValueError()
    ZoneInfo(x['timezone'])
   except Exception:return err(400,'invalid timezone')
  limits={'display_name':(1,80),'status_text':(1,140),'status_emoji':(1,32),'avatar_url':(1,512)}
  for k,v in x.items():
   if k in limits and (not isinstance(v,str) or not limits[k][0]<=len(v)<=limits[k][1]):return err(400,'invalid length')
  with core.tx() as q:
   for k,v in x.items():q.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
   z=q.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone();q.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps({'type':'user.updated','user':core.user_json(z)}),))
  return resp({'user':core.user_json(z)})


 c=core.connect(); path=r.path; m=re.match(r'/api/workspaces/([^/]+)$',path)
 if path=='/api/workspaces' and r.method=='GET':
  a=c.execute('SELECT w.* FROM workspaces w JOIN members m ON w.id=m.workspace_id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return resp({'workspaces':[dict(z) for z in a]})
 if path=='/api/workspaces' and r.method=='POST':
  if not x or not re.fullmatch(r'[a-z0-9-]{2,32}',str(x.get('slug',''))):c.close();return err(400,'invalid slug')
  try:
   with core.tx() as q:
    cur=q.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x.get('name',''),u['id']));wid=cur.lastrowid;q.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));cur=q.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=cur.lastrowid;q.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
   return resp({'workspace':{'id':wid,'slug':x['slug'],'name':x.get('name',''),'owner_id':u['id'],'join_mode':'open'},'general_channel':{'id':cid,'workspace_id':wid,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False}},201)
  except: return err(409,'duplicate')
 if m:
  w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
  if not w:c.close();return err(404,'workspace not found')
  if r.method=='GET':
   chans=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return resp({'workspace':dict(w),'channels':[core.channel_json(z) for z in chans],'read_state':{}})
 if re.match(r'/api/workspaces/[^/]+/channels$',path) and r.method=='POST':
  slug=path.split('/')[3];w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
  if not w:c.close();return err(404,'workspace not found')
  if not x or not isinstance(x.get('name'),str) or not x['name'].strip():c.close();return err(400,'invalid name')
  if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],x['name'])).fetchone():c.close();return err(409,'duplicate channel')
  try:
   with core.tx() as q:
    cur=q.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')));cid=cur.lastrowid;q.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
   c2=core.connect();z=c2.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();c2.close();return resp({'channel':core.channel_json(z)},201)
  except: return err(409,'duplicate channel')
 if path=='/api/dms' and r.method=='POST':
  if not isinstance(x,dict) or not isinstance(x.get('user_id'),int): c.close();return err(400,'invalid user_id')
  other=c.execute('SELECT id FROM users WHERE id=?',(x['user_id'],)).fetchone()
  if not other:c.close();return err(404,'user not found')
  a,b=sorted((u['id'],x['user_id']))
  z=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
  if z:c.close();return resp({'channel_id':z['channel_id']})
  with core.tx() as q:
   q.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)'); cid=q.execute('SELECT last_insert_rowid()').fetchone()[0]
   q.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));q.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));q.execute('INSERT INTO channel_members VALUES(?,?)',(cid,b))
  c.close();return resp({'channel_id':cid})
 if path=='/api/files' and r.method=='POST':
  try:
   rd=await r.multipart(); part=await rd.next()
   if not part or part.name!='file': c.close();return err(400,'file required')
   data=await part.read()
  except: c.close();return err(400,'invalid upload')
  if len(data)>10*1024*1024:c.close();return err(413,'file too large')
  with core.tx() as q:
   q.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),len(data),core.now_iso()))
   fid=q.execute('SELECT last_insert_rowid()').fetchone()[0]
  os.makedirs('/app/data/files',exist_ok=True);open('/app/data/files/'+str(fid),'wb').write(data)
  c2=core.connect();z=c2.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c2.close()
  return resp({'file':dict(z)},201)
 fm=re.match(r'/api/files/(\d+)(?:/(download))?$',path)
 if fm:
  fid=int(fm.group(1));z=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
  if not z:c.close();return err(404,'file not found')
  if fm.group(2):
   c.close()
   try:data=open('/app/data/files/'+str(fid),'rb').read()
   except:return err(404,'file not found')
   return web.Response(body=data,content_type=z['content_type'],headers={'Content-Disposition':f'attachment; filename="{z["filename"]}"'})
  c.close();return resp({'file':dict(z)})
 gm=re.match(r'/api/workspaces/([^/]+)/groups(?:/([^/]+))?$',path)
 if gm:
  w=c.execute('SELECT * FROM workspaces WHERE slug=?',(gm.group(1),)).fetchone()
  if not w:c.close();return err(404,'workspace not found')
  role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
  if not role:c.close();return err(403,'not a member')
  gh=gm.group(2)
  if r.method=='GET':
   a=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
   return resp({'groups':[{'id':z['id'],'handle':z['handle'],'name':z['name'],'member_user_ids':json.loads(z['user_ids'])} for z in a]})
  if r.method=='POST' and not gh:
   h=x.get('handle') if isinstance(x,dict) else None; ids=x.get('user_ids') if isinstance(x,dict) else None
   if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9_-]+',h) or not isinstance(ids,list) or any(not isinstance(i,int) for i in ids):c.close();return err(400,'invalid group')
   for i in ids:
    if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],i)).fetchone():c.close();return err(400,'user not member')
   if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone():c.close();return err(409,'duplicate handle')
   with core.tx() as q:q.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],h,x.get('name',''),u['id'],json.dumps(ids)));gid=q.execute('SELECT last_insert_rowid()').fetchone()[0]
   c.close();return resp({'group':{'id':gid,'handle':h,'name':x.get('name',''),'member_user_ids':ids}},201)
  z=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],gh)).fetchone()
  if not z:
   if r.method=='DELETE': c.close();return resp({'deleted':True})
   c.close();return err(404,'group not found')
  if r.method=='PATCH':
   if z['creator_id']!=u['id'] and w['owner_id']!=u['id']:c.close();return err(403,'forbidden')
   ids=x.get('user_ids',json.loads(z['user_ids']))
   if any(not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],i)).fetchone() for i in ids):c.close();return err(400,'user not member')
   c.execute('UPDATE groups_ SET name=?,user_ids=? WHERE id=?',(x.get('name',z['name']),json.dumps(ids),z['id']));c.close();return resp({'group':{'id':z['id'],'handle':gh,'name':x.get('name',z['name']),'member_user_ids':ids}})
  if r.method=='DELETE':
   if z['creator_id']!=u['id'] and w['owner_id']!=u['id']:c.close();return err(403,'forbidden')
   c.execute('DELETE FROM groups_ WHERE id=?',(z['id'],));c.close();return resp({'deleted':True})
 mm=re.match(r'/api/messages/(\d+)$',path)
 if mm and r.method in ('PATCH','DELETE'):
  mid=int(mm.group(1)); msg=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not msg:c.close();return err(404,'message not found')
  chm=c.execute('SELECT * FROM channels WHERE id=?',(msg['channel_id'],)).fetchone()
  owner=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(chm['workspace_id'],)).fetchone() if chm['workspace_id'] else None
  if r.method=='PATCH':
   if msg['deleted']:c.close();return err(409,'message deleted')
   if msg['author_id']!=u['id']:c.close();return err(403,'author required')
   if not isinstance(x,dict) or not isinstance(x.get('body'),str) or not x['body'].strip():c.close();return err(400,'invalid body')
   ment=[]
   for un in re.findall(r'(?<![\\w@])@([A-Za-z0-9_]+)',x['body']):
    zz=c.execute('SELECT id FROM users WHERE username=?',(un,)).fetchone()
    if zz and zz['id']!=u['id'] and access(c,msg['channel_id'],zz['id']) and zz['id'] not in ment:ment.append(zz['id'])
   with core.tx() as q:
    q.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(x['body'],core.now_iso(),json.dumps(ment),mid)); z=q.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();core.emit(q,msg['channel_id'],'message.updated',{'message':core.msg_json(q,z)})
   c.close();return resp({'message':core.msg_json(core.connect(),z)})
  if msg['author_id']!=u['id'] and (not owner or owner['owner_id']!=u['id']):c.close();return err(403,'forbidden')
  if not msg['deleted']:
   with core.tx() as q:q.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));core.emit(q,msg['channel_id'],'message.deleted',{'message_id':mid})
  c.close();return resp({'deleted':True})
 rm=re.match(r'/api/messages/(\d+)/reactions(?:/(.*))?$',path)
 if rm:
  mid=int(rm.group(1)); emoji=urllib.parse.unquote(rm.group(2) or '') if rm.group(2) else (x.get('emoji') if isinstance(x,dict) else '')
  msg=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not msg:c.close();return err(404,'message not found')
  ch0=c.execute('SELECT * FROM channels WHERE id=?',(msg['channel_id'],)).fetchone()
  if not access(c,ch0['id'],u['id']):c.close();return err(403,'not a member')
  if not isinstance(emoji,str) or not emoji.strip():c.close();return err(400,'invalid emoji')
  changed=0
  with core.tx() as q:
   if r.method=='POST':
    cur=q.execute('INSERT OR IGNORE INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)',(mid,u['id'],emoji)); changed=cur.rowcount
    if changed: core.emit(q,ch0['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
   elif r.method=='DELETE':
    cur=q.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)); changed=cur.rowcount
    if changed: core.emit(q,ch0['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
   out=reactions(q,mid)
  c.close();return resp({'reactions':out})
 rmrep=re.match(r'/api/messages/(\d+)/replies$',path)
 if rmrep and r.method=='GET':
  pid=int(rmrep.group(1)); parent=c.execute('SELECT * FROM messages WHERE id=?',(pid,)).fetchone()
  if not parent:c.close();return err(404,'message not found')
  if not access(c,parent['channel_id'],u['id']):c.close();return err(403,'not a member')
  a=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(pid,)).fetchall();out=[core.msg_json(c,z) for z in a];c.close();return resp({'replies':out,'next_cursor':None})
 cm=re.match(r'/api/channels/(\d+)(?:/(.*))?$',path)
 if cm:
  cid=int(cm.group(1));tail=cm.group(2) or '';ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  if not ch:c.close();return err(404,'channel not found')
  if not access(c,ch['id'],u['id']) and tail not in ('join','members'):
   c.close();return err(403,'not a member')
  if tail=='' and r.method=='GET':
   c.close();return resp({'channel':core.channel_json(ch)})
  if tail=='' and r.method=='PATCH':
   if not isinstance(x,dict) or not isinstance(x.get('topic'),str) or len(x['topic'])>250:c.close();return err(400,'invalid topic')
   c.execute('UPDATE channels SET topic=? WHERE id=?',(x['topic'],cid));z=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();c.close();return resp({'channel':core.channel_json(z)})
  if tail=='join' and r.method=='POST':
   if ch['workspace_id']:
    wj=c.execute('SELECT join_mode FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if wj and wj['join_mode']=='open': c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
   c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id']));c.close();return resp({'channel':core.channel_json(ch)})
  if tail=='leave' and r.method=='POST': c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id']));c.close();return resp({'channel':core.channel_json(ch)})
  if tail=='members':
   a=c.execute('SELECT u.* FROM users u JOIN channel_members m ON u.id=m.user_id WHERE m.channel_id=?',(cid,)).fetchall();c.close();return resp({'members':[core.user_json(z) for z in a]})
  if tail=='' and r.method=='GET':
   a=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(cid,)).fetchall();c.close();return resp({'messages':[core.msg_json(core.connect(),z) for z in a],'next_cursor':None})
  if tail=='messages' and r.method=='GET':
   a=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(cid,)).fetchall(); out=[core.msg_json(c,z) for z in a];c.close();return resp({'messages':out,'next_cursor':None})
  if tail=='messages' and r.method=='POST':
   if ch['is_archived'] and not (isinstance(x,dict) and isinstance(x.get('body'),str) and x.get('body').strip()=='/unarchive'):
    c.close();return err(423,'channel archived')
   if not isinstance(x,dict) or not isinstance(x.get('body'),str) or not x['body'].strip():c.close();return err(400,'empty body')
   bodytxt=x['body']
   if bodytxt.startswith('//'): bodytxt=bodytxt[1:]
   elif bodytxt.startswith('/'):
    parts=bodytxt.split(' ',1);cmd=parts[0];arg=parts[1] if len(parts)>1 else ''
    if cmd=='/me' and not arg.strip():c.close();return err(400,'text required')
    if cmd=='/shrug':bodytxt=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
    elif cmd=='/topic':
     if not arg.strip():c.close();return err(400,'topic required')
     c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,cid));z=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();c.close();return resp({'message':None,'channel':core.channel_json(z)},201)
    elif cmd=='/invite':
     q=c.execute('SELECT * FROM users WHERE username=?',(arg.strip().lstrip('@'),)).fetchone()
     if not q:c.close();return err(404,'user not found')
     c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,q['id']));c.close();return resp({'message':None,'invited_user':core.user_json(q)},201)
    elif cmd in ('/archive','/unarchive'):
     w=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
     if not w or w['owner_id']!=u['id']:c.close();return err(403,'owner required')
     c.execute('UPDATE channels SET is_archived=? WHERE id=?',(cmd=='/archive',cid));z=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();c.close();return resp({'message':None,'channel':core.channel_json(z)},201)
    elif cmd not in ('/me','/shrug'):c.close();return err(400,'unknown command')
   ids=x.get('file_ids',[])
   if not isinstance(ids,list) or any(not isinstance(i,int) for i in ids): c.close();return err(400,'invalid file_ids')
   for fid in ids:
    f=c.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone()
    if not f:c.close();return err(400,'invalid attachment')
   mention_ids=set()
   for uname in re.findall(r'(?<![\\w@])@([A-Za-z0-9_]+)',bodytxt):
    if uname in ('channel','here'): continue
    q0=c.execute('SELECT id FROM users WHERE username=?',(uname,)).fetchone()
    if q0 and access(c,cid,q0['id']) and q0['id']!=u['id']:mention_ids.add(q0['id'])
   for gh,gu in c.execute('SELECT handle,user_ids FROM groups_ WHERE workspace_id=?',(ch['workspace_id'],)).fetchall():
    if '@'+gh in bodytxt:
     for gid in json.loads(gu):
      if gid!=u['id'] and access(c,cid,gid): mention_ids.add(gid)
   if '@channel' in bodytxt or '@here' in bodytxt:
    active={z['user_id'] for z in c.execute('SELECT user_id FROM presence WHERE last_seen>?',(asyncio.get_event_loop().time()-10,)).fetchall()}
    rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(cid,)).fetchall()
    for rr in rows:
     if rr['user_id']!=u['id'] and ('@channel' in bodytxt or rr['user_id'] in active):mention_ids.add(rr['user_id'])
   with core.tx() as q:
    cur=q.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,u['id'],bodytxt,x.get('parent_id'),core.now_iso(),json.dumps(sorted(mention_ids))));mid=cur.lastrowid
    for fid in ids:q.execute('UPDATE files SET message_id=? WHERE id=?',(mid,fid))
    z=q.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();core.emit(q,cid,'message.reply' if x.get('parent_id') else 'message.created',{'message':core.msg_json(q,z)})
   return resp({'message':core.msg_json(core.connect(),z)},201)
 c.close(); return err(404,'not found')
async def ws_handler(r):
 u=core.user_for_token(r.query.get('token',''))
 if not u:return err(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(r); subs={}; running=True; conn_id=secrets.token_urlsafe(12); pc=core.connect();pc.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],asyncio.get_event_loop().time()));pc.close()
 async def sender():
  global_last=0
  while not ws.closed:
   c=core.connect()
   grows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_last,)).fetchall()
   for gz in grows:
    await ws.send_str(gz['payload']); global_last=gz['id']
   for cid,last in list(subs.items()):
    rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
    for z in rows: await ws.send_str(z['payload']);subs[cid]=z['event_id']
   c.close();await asyncio.sleep(.05)
 task=asyncio.create_task(sender())
 try:
  async for msg in ws:
   if msg.type==WSMsgType.TEXT:
    try:x=json.loads(msg.data)
    except:continue
    cid=int(x.get('channel_id',0))
    if x.get('type')=='subscribe':
     c=core.connect();head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone();c.close();subs[cid]=head[0] if head else 0;await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
    elif x.get('type')=='resume':
     since=int(x.get('since_event_id',0))
     c2=core.connect()
     rows=c2.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall()
     headrow=c2.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()
     c2.close()
     for ev in rows:
      await ws.send_str(ev['payload'])
     head=int(headrow[0]) if headrow else since
     subs[cid]=head
     await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':head})
 finally:
  pc=core.connect();pc.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,));pc.close(); task.cancel()
 return ws
app=web.Application();app.router.add_get('/api/ws',ws_handler);app.router.add_route('*','/api/{tail:.*}',api);app.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'));web.run_app(app,host='127.0.0.1',port=8000+NODE)
