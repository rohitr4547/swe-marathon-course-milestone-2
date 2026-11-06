import asyncio,json,re,os,sys,uuid,base64
from aiohttp import web,WSMsgType
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
core.init_db()
def out(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return out({'error':msg},status)
async def body(req):
 try:return await req.json()
 except:return None
def auth(req):
 h=req.headers.get('Authorization','')
 return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
def need(req):
 u=auth(req); return u
def cj(r): return core.channel_to_json(r)
def wj(r): return core.workspace_to_json(r)
def uj(r): return core.user_to_json(r)
def member(cid,uid):
 c=core.connect();x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone();c.close();return bool(x)
def ws_member(wid,uid): return bool(core.member_role(wid,uid))
def checkchan(u,ch):
 return ch and core.can_read_channel(u['id'],ch)
async def register(req):
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
   uid=cur.lastrowid;t=core.issue_token(c,uid);u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
  return out({'user':uj(u),'token':t},201)
 except: return err(409,'username exists')
async def login(req):
 b=await body(req); c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username'),)) .fetchone() if isinstance(b,dict) else None;c.close()
 if not u or not core.verify_password(b.get('password',''),u['password']):return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return out({'user':uj(u),'token':t})
async def me(req):
 u=need(req);return out({'user':uj(u)}) if u else err(401,'unauthorized')
async def health(req):return out({'status':'ok','node_id':NODE})
async def createws(req):
 u=need(req)
 if not u:return err(401,'unauthorized')
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')) or not isinstance(b.get('name'),str):return err(400,'invalid')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']));wid=cur.lastrowid
   c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'));cid=core.create_channel_row(c,wid,'general');core.add_channel_member(c,cid,u['id'])
   ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()
  return out({'workspace':wj(w),'general_channel':cj(ch)},201)
 except:return err(409,'duplicate')
async def workspaces(req):
 u=need(req)
 if not u:return err(401,'unauthorized')
 c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return out({'workspaces':[wj(x) for x in rs]})
async def workspace(req):
 u=need(req);w=core.get_workspace(req.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if not ws_member(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();chs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],req.query.get('include_archived') in ('1','true'))).fetchall();c.close()
 return out({'workspace':wj(w),'channels':[cj(x) for x in chs],'read_state':{}})
async def members(req):
 u=need(req);w=core.get_workspace(req.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if not ws_member(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT m.*,u.username,u.display_name FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close();return out({'members':[{'user_id':x['user_id'],'role':x['role'],'username':x['username'],'display_name':x['display_name']} for x in rs]})
async def createch(req):
 u=need(req);w=core.get_workspace(req.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if not ws_member(w['id'],u['id']):return err(403,'forbidden')
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b.get('name','')):return err(400,'invalid')
 role=core.member_role(w['id'],u['id']); private=bool(b.get('is_private',False))
 if private and role not in ('owner','admin'):return err(403,'forbidden')
 try:
  with core.tx() as c:cid=core.create_channel_row(c,w['id'],b['name'],private,b.get('topic',''));core.add_channel_member(c,cid,u['id']);r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  return out({'channel':cj(r)},201)
 except:return err(409,'duplicate')
async def channeljoin(req):
 u=need(req);ch=core.get_channel(req.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if ch['workspace_id'] and not ws_member(ch['workspace_id'],u['id']):
  w=core.get_workspace(str(ch['workspace_id'])) # unused
  c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
  if w['join_mode']!='open':return err(403,'forbidden')
 with core.tx() as c:
  if ch['workspace_id']:core.add_workspace_member(c,ch['workspace_id'],u['id'])
  core.add_channel_member(c,ch['id'],u['id'])
 return out({'channel':cj(core.get_channel(ch['id']))})
async def channelmembers(req):
 u=need(req);ch=core.get_channel(req.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not checkchan(u,ch):return err(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close();return out({'members':[uj(x) for x in rs]})
async def messages(req):
 u=need(req);ch=core.get_channel(req.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not checkchan(u,ch):return err(403,'forbidden')
 try:lim,cur=core.parse_paging(req.query,'msg')
 except:return err(400,'bad cursor')
 rs,n=core.list_messages(ch['id'],lim,cur)
 return out({'messages':[core.message_to_json(x) for x in rs],'next_cursor':n})
async def postmsg(req):
 u=need(req);ch=core.get_channel(req.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not checkchan(u,ch):return err(403,'forbidden')
 if ch['is_archived'] and not (isinstance((await body(req)),dict) and isinstance((await body(req)).get('body'),str) and (await body(req)).get('body','').strip().startswith('/unarchive')):return err(423,'archived')
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty')
 file_ids=b.get('file_ids',[])
 if not isinstance(file_ids,list) or any(not isinstance(x,int) for x in file_ids): return err(400,'invalid files')
 if file_ids:
  c=core.connect()
  for fid in file_ids:
   f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
   if not f or f['uploader_id']!=u['id'] or f['message_id'] is not None:
    c.close(); return err(400,'invalid files')
  c.close()
 text=b['body']
 if text.startswith('//'):text=text[1:]
 elif text.startswith('/'):
  p=text.split(' ',1);cmd=p[0];arg=p[1] if len(p)>1 else ''
  if cmd=='/me' and not arg.strip():return err(400,'missing text')
  if cmd=='/me': pass
  elif cmd=='/shrug':text=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
  elif cmd=='/topic':
   if not arg:return err(400,'missing')
   with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']))
   return out({'message':None,'channel':cj(core.get_channel(ch['id']))},201)
  elif cmd in ('/archive','/unarchive'):
   if core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
   with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(cmd=='/archive',ch['id']))
   return out({'message':None,'channel':cj(core.get_channel(ch['id']))},201)
  elif cmd=='/invite':
   target=arg.lstrip('@');c=core.connect();x=c.execute('SELECT * FROM users WHERE username=?',(target,)).fetchone();c.close()
   if not x:return err(400,'unknown')
   with core.tx() as c:core.add_channel_member(c,ch['id'],x['id'])
   return out({'message':None,'invited_user':uj(x)},201)
  else:return err(400,'unknown')
 try:
  r=core.create_message(u,ch['id'],text,b.get('parent_id'))
  if file_ids:
   with core.tx() as c:
    c.executemany('UPDATE files SET message_id=? WHERE id=?',[(r['id'],fid) for fid in file_ids])
  return out({'message':core.message_to_json(r)},201)
 except:return err(400,'invalid')
async def replies(req):
 u=need(req);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
 if not u:return err(401,'unauthorized')
 if not m:return err(404,'not found')
 try:lim,cur=core.parse_paging(req.query,'rep')
 except:return err(400,'bad cursor')
 rs,n=core.list_messages(m['channel_id'],lim,cur,True);return out({'replies':[core.message_to_json(x) for x in rs if x['parent_id']==m['id']],'next_cursor':n})
async def reaction(req):
 u=need(req);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
 if not u:return err(401,'unauthorized')
 if not m:return err(404,'not found')
 if not member(m['channel_id'],u['id']):return err(403,'forbidden')
 emoji=req.match_info.get('emoji')
 if req.method=='POST':
  b=await body(req);emoji=b.get('emoji') if isinstance(b,dict) else None
  if not isinstance(emoji,str) or not emoji:return err(400,'invalid')
 return out({'reactions':core.react(u,m['id'],emoji,req.method=='POST')})
async def missing_auth(req):
    if not need(req): return err(401,'unauthorized')
    return err(404,'not found')

async def dms(req):
    u=need(req)
    if not u: return err(401,'unauthorized')
    b=await body(req)
    if not isinstance(b,dict) or not isinstance(b.get('user_id'),int): return err(400,'invalid user_id')
    other=core.user_for_token(None)
    c=core.connect()
    other=c.execute('SELECT * FROM users WHERE id=?',(b['user_id'],)).fetchone()
    c.close()
    if not other: return err(404,'not found')
    if other['id']==u['id']: return err(400,'invalid user_id')
    a,bb=sorted((u['id'],other['id']))
    c=core.connect(); row=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bb)).fetchone(); c.close()
    if row: return out({'channel_id':row['channel_id']})
    with core.tx() as c:
        c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',(f'dm-{a}-{bb}',1))
        cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,bb,cid))
        c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a))
        c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,bb))
    return out({'channel_id':cid})

async def group_one(req):
    u=need(req); w=core.get_workspace(req.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    if not ws_member(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); g=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone(); c.close()
    if not g:
        if req.method=='DELETE': return out({'deleted':True})
        return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if u['id']!=g['creator_id'] and role!='owner':return err(403,'forbidden')
    if req.method=='DELETE':
        with core.tx() as c:
            c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups_tbl WHERE id=?',(g['id'],))
        return out({'deleted':True})
    b=await body(req)
    if not isinstance(b,dict):return err(400,'invalid')
    with core.tx() as c:
        if 'name' in b:
            if not isinstance(b['name'],str):return err(400,'invalid')
            c.execute('UPDATE groups_tbl SET name=? WHERE id=?',(b['name'],g['id']))
        if 'user_ids' in b:
            if not isinstance(b['user_ids'],list):return err(400,'invalid')
            valid={x['user_id'] for x in c.execute('SELECT user_id FROM memberships WHERE workspace_id=?',(w['id'],))}
            if any(x not in valid for x in b['user_ids']):return err(400,'invalid')
            c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
            c.executemany('INSERT INTO group_members VALUES(?,?)',[(g['id'],x) for x in b['user_ids']])
        g2=c.execute('SELECT * FROM groups_tbl WHERE id=?',(g['id'],)).fetchone()
    return out({'group':core.group_to_json(g2)})

async def upload(req):
 u=need(req)
 if not u:return err(401,'unauthorized')
 data=await req.post();f=data.get('file')
 if not f:return err(400,'file')
 raw=f.file.read()
 if len(raw)>10*1024*1024:return err(413,'too large')
 with core.tx() as c:
  cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],f.filename,f.content_type,len(raw),core.now_iso()));fid=cur.lastrowid
 with open(f'/app/data/files/{fid}','wb') as x:x.write(raw)
 return out({'file':core.file_to_json(core.connect().execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone())},201)
async def getfile(req):
 u=need(req)
 if not u:return err(401,'unauthorized')
 c=core.connect();f=c.execute('SELECT * FROM files WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
 if not f:return err(404,'not found')
 if req.path.endswith('/download'):
  return web.Response(body=open(f'/app/data/files/{f["id"]}','rb').read(),content_type=f['content_type'],headers={'Content-Disposition':f'attachment; filename="{f["filename"]}"'})
 return out({'file':core.file_to_json(f)})
async def groups(req):
 u=need(req);w=core.get_workspace(req.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if not ws_member(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect()
 if req.method=='GET':
  rs=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return out({'groups':[core.group_to_json(x) for x in rs]})
 b=await body(req);handle=b.get('handle') if isinstance(b,dict) else ''
 if not isinstance(handle,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',handle):return err(400,'invalid')
 ids=b.get('user_ids',[]); valid={x['user_id'] for x in c.execute('SELECT user_id FROM memberships WHERE workspace_id=?',(w['id'],))}
 c.close()
 if not isinstance(ids,list) or any(x not in valid for x in ids):return err(400,'invalid')
 try:
  with core.tx() as c:r=core.create_group(c,w['id'],handle,b.get('name',''),ids,u['id'])
  return out({'group':core.group_to_json(r)},201)
 except:return err(409,'duplicate')
async def profile(req):
 u=need(req)
 if not u:return err(401,'unauthorized')
 if req.method=='GET':
  c=core.connect();x=c.execute('SELECT * FROM users WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
  return out({'user':uj(x)}) if x else err(404,'not found')
 b=await body(req)
 if not isinstance(b,dict):return err(400,'invalid')
 for k,v in b.items():
  if k in ('display_name','timezone','avatar_url','status_text','status_emoji') and not isinstance(v,str):return err(400,'invalid')
  if k=='timezone':
   try:__import__('zoneinfo').ZoneInfo(v)
   except:return err(400,'invalid')
  if k=='display_name' and not 1<=len(v)<=80:return err(400,'invalid')
  if k=='status_text' and len(v)>140:return err(400,'invalid')
  if k=='status_emoji' and len(v)>32:return err(400,'invalid')
  if k=='avatar_url' and len(v)>512:return err(400,'invalid')
 return out({'user':uj(core.update_profile(u,b))})
async def ws(req):
 u=core.user_for_token(req.query.get('token')); 
 if not u:return err(401,'unauthorized')
 sock=web.WebSocketResponse();await sock.prepare(req);cid=str(uuid.uuid4());core.touch_presence(cid,u['id']);subs={};glob=0
 try:
  async def sender():
   nonlocal glob
   while not sock.closed:
    for ch,since in list(subs.items()):
     for e in core.events_after(ch,since): await sock.send_json(e);subs[ch]=e['event_id']
    for i,e in core.globals_after(glob):await sock.send_json(e);glob=i
    core.touch_presence(cid,u['id']);await asyncio.sleep(.05)
  task=asyncio.create_task(sender())
  async for msg in sock:
   if msg.type==WSMsgType.TEXT:
    x=json.loads(msg.data);ch=int(x.get('channel_id',0))
    if x.get('type')=='subscribe':subs[ch]=core.head_event_id(ch);await sock.send_json({'type':'subscribed','channel_id':ch,'head_event_id':subs[ch]})
    elif x.get('type')=='resume':
     since=int(x.get('since_event_id',0));subs[ch]=since
     for e in core.events_after(ch,since):await sock.send_json(e);subs[ch]=e['event_id']
     await sock.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
  task.cancel()
 finally:core.drop_presence(cid)
 return sock
app=web.Application(client_max_size=12*1024*1024)
for path,fn,methods in [
('/api/auth/register',register,['POST']),('/api/auth/login',login,['POST']),('/api/auth/me',me,['GET']),('/api/health',health,['GET']),('/api/workspaces',workspaces,['GET']),('/api/workspaces',createws,['POST']),
('/api/files',upload,['POST']),('/api/dms',dms,['POST']),('/api/users/{id}',profile,['GET']),('/api/users/me',profile,['PATCH']),('/api/workspaces/{slug}',workspace,['GET']),('/api/workspaces/{slug}/members',members,['GET']),('/api/workspaces/{slug}/channels',createch,['POST']),('/api/workspaces/{slug}/groups',groups,['GET','POST']),('/api/channels/{id}/join',channeljoin,['POST']),('/api/channels/{id}/members',channelmembers,['GET']),('/api/channels/{id}/messages',messages,['GET']),('/api/channels/{id}/messages',postmsg,['POST']),('/api/messages/{id}/replies',replies,['GET']),('/api/messages/{id}/reactions',reaction,['POST']),('/api/messages/{id}/reactions/{emoji}',reaction,['DELETE']),('/api/files/{id}',getfile,['GET']),('/api/files/{id}/download',getfile,['GET'])]:
 for method in methods: app.router.add_route(method,path,fn)
for _method,_path in [('POST','/api/dms'),('PATCH','/api/workspaces/{slug}'),('POST','/api/workspaces/{slug}/invitations'),('GET','/api/workspaces/{slug}/invitations'),]:
 app.router.add_route(_method,_path,missing_auth)
app.router.add_route('PATCH','/api/workspaces/{slug}/groups/{handle}',group_one)
app.router.add_route('DELETE','/api/workspaces/{slug}/groups/{handle}',group_one)
app.router.add_get('/api/ws',ws)
app.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'))
web.run_app(app,host='127.0.0.1',port=8000+NODE)
