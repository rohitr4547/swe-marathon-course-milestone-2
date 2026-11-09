import asyncio,json,os,re,sys,uuid,base64,zoneinfo,time
from aiohttp import web,WSMsgType
import core
core.init_db(); NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
WSS=set()
def js(x,status=200): return web.json_response(x,status=status)
def err(s,m): return js({'error':m},s)
async def body(r):
 try:return await r.json()
 except:return None
def user(r):
 h=r.headers.get('Authorization','');return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
def auth(r):
 u=user(r);return u
def _valid_zone(v):
 try: zoneinfo.ZoneInfo(v); return True
 except Exception: return False
def member(c,ch,uid):
 x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,uid)).fetchone();return bool(x)
async def ws_handler(r):
 tok=r.query.get('token',''); u=core.user_for_token(tok)
 if not u:return err(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(r); subs={}; alive=True; global_seen=0; connid=str(uuid.uuid4())
 with core.tx() as z:z.execute('INSERT INTO presence VALUES(?,?,?)',(connid,u['id'],time.time()))
 async def poll():
  nonlocal global_seen
  tick=0
  while not ws.closed:
   try:
    tick+=1
    if tick % 20 == 0:
     pc=core.connect();pc.execute('UPDATE presence SET last_seen=? WHERE conn_id=?',(time.time(),connid));pc.commit();pc.close()
    gc=core.connect(); grows=gc.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_seen,)).fetchall();gc.close()
    for gr in grows:
     await ws.send_str(gr['payload'])
     global_seen=gr['id']
    for ch,last in list(subs.items()):
     ec=core.connect(); rows=ec.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)).fetchall();ec.close()
     for row in rows:
      await ws.send_str(row['payload']);subs[ch]=row['event_id']
   except Exception:
    await asyncio.sleep(.05)
   await asyncio.sleep(.05)

 task=asyncio.create_task(poll())
 try:
  async for msg in ws:
   if msg.type==WSMsgType.TEXT:
    try:b=json.loads(msg.data)
    except:continue
    if b.get('type')=='subscribe':
     ch=int(b.get('channel_id'));c=core.connect();head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close()
     if head is None: continue
     subs[ch]=head[0];await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head[0]})
    elif b.get('type')=='resume':
     ch=int(b.get('channel_id'));since=int(b.get('since_event_id',0));c=core.connect()
     rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall();head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0];c.close()
     for row in rows: await ws.send_str(row['payload'])
     subs[ch]=head;await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':head})
 finally:
  task.cancel()
  with core.tx() as z:z.execute('DELETE FROM presence WHERE conn_id=?',(connid,))
 return ws
async def api(r):
 p=r.path.split('/')
 if len(p)>2 and p[2]=='health' and r.method=='GET':
  return js({'status':'ok','node_id':NODE})
 if len(p)>3 and p[2]=='auth' and p[3] in ('register','login'):
  b=await body(r) or {}; un=b.get('username');pw=b.get('password')
  if p[3]=='register':
   if not isinstance(un,str) or not re.fullmatch(r'[A-Za-z0-9_]+',un) or not isinstance(pw,str) or len(pw)<8:return err(400,'invalid')
   try:
    with core.tx() as z:
     z.execute('INSERT INTO users(username,password,display_name,timezone,avatar_url,status_text,status_emoji) VALUES(?,?,?,?,?,?,?)',(un,core.hash_password(pw),b.get('display_name') or un,'UTC','','',''))
     uid=z.lastrowid;tok=core.issue_token(z,uid)
   except Exception:return err(409,'duplicate')
   return js({'user':core.user_to_json(core.user_for_token(tok)),'token':tok},201)
  x=core.connect().execute('SELECT * FROM users WHERE username=?',(un,)).fetchone()
  if not x or not core.verify_password(pw or '',x['password']):return err(401,'wrong password')
  with core.tx() as z:tok=core.issue_token(z,x['id'])
  return js({'user':core.user_to_json(x),'token':tok})
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); method=r.method
 try:
  if p[2]=='workspaces' and method=='POST' and len(p)==3:
   b=await body(r) or {}; slug=b.get('slug'); name=b.get('name')
   if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not isinstance(name,str) or not name:return err(400,'invalid')
   try:
    with core.tx() as z:
     z.execute('INSERT INTO workspaces(slug,name,owner_id,join_mode) VALUES(?,?,?,?)',(slug,name,u['id'],'open')); wid=z.lastrowid
     z.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'))
     z.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); gid=z.lastrowid
     z.execute('INSERT INTO channel_members VALUES(?,?)',(gid,u['id']))
   except Exception:return err(409,'duplicate')
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();g=c.execute('SELECT * FROM channels WHERE id=?',(gid,)).fetchone()
   return js({'workspace':core.workspace_to_json(w),'general_channel':core.channel_json(g)},201)
  if p[2]=='workspaces' and method=='GET' and len(p)==3:
   rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON w.id=m.workspace_id WHERE m.user_id=?',(u['id'],)).fetchall()
   return js({'workspaces':[core.workspace_to_json(x) for x in rows]})
  if p[2]=='workspaces' and len(p)>=4:
   slug=p[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return err(404,'not found')
   role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if method=='GET' and len(p)==4:
    chans=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall()
    return js({'workspace':core.workspace_to_json(w),'channels':[core.channel_json(x) for x in chans],'read_state':[]})
   if len(p)==4 and method=='GET': return js({'workspace':core.workspace_to_json(w)})
   if len(p)==5 and p[4]=='members' and method=='GET':
    ms=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall();return js({'members':[dict(x) for x in ms]})
   if len(p)==5 and p[4]=='channels' and method=='POST':
    if not role:return err(403,'not member')
    b=await body(r) or {}; n=b.get('name')
    if not isinstance(n,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',n):return err(400,'invalid')
    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],n)).fetchone():return err(409,'duplicate')
    priv=bool(b.get('is_private',False))
    if priv and role['role'] not in ('owner','admin'):return err(403,'forbidden')
    with core.tx() as z:
     z.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],n,int(priv),b.get('topic',''))); cid=z.lastrowid;z.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
    return js({'channel':core.channel_json(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
  if p[2]=='channels' and len(p)>=4:
   try:cid=int(p[3])
   except:return err(404,'not found')
   ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   if not ch:return err(404,'not found')
   if method=='POST' and len(p)==5 and p[4]=='join':
    if ch['is_private'] and not member(c,cid,u['id']):return err(403,'private')
    with core.tx() as z:
     if ch['workspace_id'] and not z.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone():z.execute('INSERT INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
     z.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id']))
    return js({'channel':core.channel_json(ch)})
   if method=='GET' and len(p)==5 and p[4]=='members':
    rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON u.id=m.user_id WHERE m.channel_id=?',(cid,)).fetchall();return js({'members':[core.user_to_json(x) for x in rows]})
   if method=='POST' and len(p)==5 and p[4]=='messages':
    if not member(c,cid,u['id']):return err(403,'not member')
    b=await body(r) or {}; text=b.get('body')
    if not isinstance(text,str) or not text:return err(400,'empty')
    owner = bool(ch['workspace_id'] and c.execute('SELECT 1 FROM workspaces WHERE id=? AND owner_id=?',(ch['workspace_id'],u['id'])).fetchone())
    if text.startswith('/'):
     if text.startswith('//'): text=text[1:]
     elif text.startswith('/me '):
      if not text[4:].strip(): return err(400,'empty command')
     elif text == '/me' or text.startswith('/me   '): return err(400,'empty command')
     elif text.startswith('/shrug'):
      tail=text[6:].strip(); text=(tail+' ' if tail else '')+'¯\\_(ツ)_/¯'
     elif text.startswith('/topic '):
      topic=text[7:]
      if not topic.strip() or len(topic)>250:return err(400,'invalid topic')
      if not member(c,cid,u['id']):return err(403,'not member')
      with core.tx() as z:z.execute('UPDATE channels SET topic=? WHERE id=?',(topic,cid))
      row=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();return js({'message':None,'channel':core.channel_json(row)},201)
     elif text.startswith('/invite '):
      if not text[8:].startswith('@'):return err(400,'invalid invite')
      name=text[9:].strip(); target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
      if not target:return err(404,'user not found')
      with core.tx() as z:z.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,target['id']))
      return js({'message':None,'invited_user':core.user_to_json(target)},201)
     elif text in ('/archive','/unarchive'):
      if not owner:return err(403,'forbidden')
      val=1 if text=='/archive' else 0
      with core.tx() as z:z.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid))
      row=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();return js({'message':None,'channel':core.channel_json(row)},201)
     else:return err(400,'unknown command')
    if ch['is_archived'] and not (text=='/unarchive' and owner):return err(423,'archived')
    fids=b.get('file_ids',[])
    if not isinstance(fids,list) or any(not isinstance(x,int) for x in fids):return err(400,'invalid files')
    if fids:
     marks=','.join('?'*len(fids)); owned=c.execute(f'SELECT id FROM files WHERE id IN ({marks}) AND uploader_id=? AND message_id IS NULL',(*fids,u['id'])).fetchall()
     if len(owned)!=len(set(fids)):return err(400,'invalid files')
    with core.tx() as z:
     z.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],text,b.get('parent_id'),core.now_iso()));mid=z.lastrowid
     for fid in fids:z.execute('UPDATE files SET message_id=? WHERE id=?',(mid,fid))
     ev=core.emit(z,cid,'message.reply' if b.get('parent_id') else 'message.created',{'message_id':mid})
    row=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();return js({'message':core.msg_json(c,row)},201)
   if method=='GET' and len(p)==5 and p[4]=='messages':
    if not member(c,cid,u['id']):return err(403,'not member')
    rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(cid,)).fetchall();return js({'messages':[core.msg_json(c,x) for x in rows],'next_cursor':None})
  if p[2]=='messages' and len(p)>=4:
   try: mid=int(p[3])
   except: return err(404,'not found')
   mr=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
   if not mr:return err(404,'not found')
   ch=c.execute('SELECT * FROM channels WHERE id=?',(mr['channel_id'],)).fetchone()
   if not member(c,ch['id'],u['id']):return err(403,'not member')
   if len(p)>=5 and p[4]=='reactions':
    if method=='POST':
     b=await body(r) or {}; emoji=b.get('emoji')
     if not isinstance(emoji,str) or not emoji:return err(400,'invalid emoji')
     exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
     if not exists:
      with core.tx() as z:
       z.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
       core.emit(z,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
     return js({'reactions':core.reactions(c,mid)})
    if method=='DELETE' and len(p)>=6:
     from urllib.parse import unquote
     emoji=unquote(p[5]); exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
     if exists:
      with core.tx() as z:
       z.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
       core.emit(z,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
     return js({'reactions':core.reactions(c,mid)})
  if p[2]=='messages' and len(p)>=5 and p[4]=='replies' and method=='GET':
   rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(int(p[3]),)).fetchall()
   return js({'replies':[core.msg_json(c,x) for x in rows],'next_cursor':None})
  if p[2]=='dms' and method=='POST':
   b=await body(r) or {}; other=b.get('user_id')
   if not isinstance(other,int): return err(400,'invalid user_id')
   otherrow=c.execute('SELECT * FROM users WHERE id=?',(other,)).fetchone()
   if not otherrow:return err(404,'user not found')
   a,bid=sorted((u['id'],other))
   old=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bid)).fetchone()
   if old:return js({'channel_id':old[0]})
   with core.tx() as z:
    z.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',('',1)); cid=z.lastrowid
    z.execute('INSERT INTO dms VALUES(?,?,?)',(a,bid,cid))
    z.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a)); z.execute('INSERT INTO channel_members VALUES(?,?)',(cid,bid))
   return js({'channel_id':cid})
  if p[2]=='files':
   if method=='POST':
    reader=await r.multipart(); part=await reader.next()
    if not part or part.name!='file':return err(400,'file required')
    data=await part.read(decode=False)
    if len(data)>10*1024*1024:return err(413,'too large')
    fn=part.filename or 'file'; ct=part.headers.get('Content-Type','application/octet-stream')
    with core.tx() as z:
     z.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],fn,ct,len(data),core.now_iso()));fid=z.lastrowid
    os.makedirs('/app/data/files',exist_ok=True);open('/app/data/files/'+str(fid),'wb').write(data)
    f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
    return js({'file':dict(f)},201)
   try:fid=int(p[3])
   except:return err(404,'not found')
   f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
   if not f:return err(404,'not found')
   if len(p)>4 and p[4]=='download':
    try:data=open('/app/data/files/'+str(fid),'rb').read()
    except:return err(404,'not found')
    return web.Response(body=data,content_type=f['content_type'],headers={'Content-Disposition':'attachment; filename="'+f['filename']+'"'})
   return js({'file':dict(f)})
  if p[2]=='workspaces' and len(p)>=5 and p[4]=='groups':
   slug=p[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return err(404,'not found')
   role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if not role:return err(403,'not member')
   if len(p)==5 and method in ('GET','POST'):
    if method=='GET':
     rows=c.execute('SELECT * FROM groupsx WHERE workspace_id=?',(w['id'],)).fetchall();return js({'groups':[core.group_json(c,x) for x in rows]})
    b=await body(r) or {};h=b.get('handle');name=b.get('name');ids=b.get('user_ids',[])
    if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',h) or not isinstance(name,str) or not isinstance(ids,list):return err(400,'invalid')
    if c.execute('SELECT 1 FROM groupsx WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone():return err(409,'duplicate')
    if any(not isinstance(x,int) or not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone() for x in ids):return err(400,'invalid members')
    with core.tx() as z:
     z.execute('INSERT INTO groupsx(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],h,name,u['id']));gid=z.lastrowid
     for x in ids:z.execute('INSERT INTO group_members VALUES(?,?)',(gid,x))
    rc=core.connect(); gr=rc.execute('SELECT * FROM groupsx WHERE id=?',(gid,)).fetchone(); out=core.group_json(rc,gr); rc.close(); return js({'group':out},201)
   if len(p)==6:
    h=p[5];g=c.execute('SELECT * FROM groupsx WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone()
    if not g:
     if method=='DELETE': return js({'deleted':True})
     return err(404,'not found')
    if role['role']!='owner' and g['creator_id']!=u['id']:return err(403,'forbidden')
    if method=='DELETE':
     with core.tx() as z:z.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));z.execute('DELETE FROM groupsx WHERE id=?',(g['id'],))
     return js({'deleted':True})
    if method=='PATCH':
     b=await body(r) or {}
     with core.tx() as z:
      if 'name' in b:
       if not isinstance(b['name'],str):return err(400,'invalid')
       z.execute('UPDATE groupsx SET name=? WHERE id=?',(b['name'],g['id']))
      if 'user_ids' in b:
       ids=b['user_ids']
       if not isinstance(ids,list) or any(not isinstance(x,int) or not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone() for x in ids):return err(400,'invalid')
       z.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
       for x in ids:z.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],x))
     rc=core.connect(); gr=rc.execute('SELECT * FROM groupsx WHERE id=?',(g['id'],)).fetchone(); out=core.group_json(rc,gr); rc.close(); return js({'group':out})
  if p[2]=='users' and p[3]=='me' and method=='PATCH':
   b=await body(r) or {}; allowed={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512),'timezone':None}
   for k,v in b.items():
    if k not in allowed or not isinstance(v,str) or (allowed[k] and not allowed[k][0]<=len(v)<=allowed[k][1]) or (k=='timezone' and not _valid_zone(v)): return err(400,'invalid')
   with core.tx() as z:
    z.execute('UPDATE users SET '+','.join(k+'=?' for k in b)+' WHERE id=?',(*b.values(),u['id']))
    fresh=z.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
    z.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps({'type':'user.updated','user':core.user_to_json(fresh)}),))
   return js({'user':core.user_to_json(core.user_for_token(r.headers['Authorization'][7:]))})
  if p[2]=='users' and method=='GET':
   x=c.execute('SELECT * FROM users WHERE id=?',(p[3],)).fetchone();return js({'user':core.user_to_json(x)}) if x else err(404,'not found')
  if p[2]=='auth' and p[3]=='me':return js({'user':core.user_to_json(u)})
  if p[2]=='auth' and p[3] in ('register','login'):
   b=await body(r) or {}; un=b.get('username');pw=b.get('password')
   if p[3]=='register':
    if not isinstance(un,str) or not re.fullmatch(r'[A-Za-z0-9_]+',un) or not isinstance(pw,str) or len(pw)<8:return err(400,'invalid')
    try:
     with core.tx() as z:z.execute('INSERT INTO users(username,password,display_name,timezone,avatar_url,status_text,status_emoji) VALUES(?,?,?,?,?,?,?)',(un,core.hash_password(pw),b.get('display_name') or un,'UTC','','','')); uid=z.lastrowid;tok=core.issue_token(z,uid)
    except: return err(409,'duplicate')
    return js({'user':core.user_to_json(core.user_for_token(tok)),'token':tok},201)
   x=c.execute('SELECT * FROM users WHERE username=?',(un,)).fetchone()
   if not x or not core.verify_password(pw or '',x['password']):return err(401,'wrong password')
   with core.tx() as z:tok=core.issue_token(z,x['id'])
   return js({'user':core.user_to_json(x),'token':tok})
  if p[2]=='health':return js({'status':'ok','node_id':NODE})
  return err(404,'not found')
 finally:c.close()
def main():
 app=web.Application()
 app.router.add_route('*','/api/ws',ws_handler)
 app.router.add_route('*','/api/{tail:.*}',api)
 app.router.add_get('/',lambda r: web.Response(text='<html><body><h1>Huddle</h1></body></html>',content_type='text/html'))
 web.run_app(app,host='127.0.0.1',port=8000+NODE)
if __name__=='__main__': main()
