from aiohttp import web,WSMsgType
import asyncio,os,time,uuid
import argparse,json,re,secrets,base64,sqlite3,sys
import core
def out(x,status=200): return web.json_response(x,status=status)
async def body(r):
 try:return await r.json()
 except:return None
def auth(r):
 h=r.headers.get('Authorization','')
 t=h[7:] if h.startswith('Bearer ') else r.query.get('token','')
 return core.token_user(t)
async def reg(r):
 b=await body(r)
 if not isinstance(b,dict) or not re.fullmatch(r'[A-Za-z0-9_]+',str(b.get('username',''))) or len(str(b.get('password','')))<8:return out({'error':'invalid'},400)
 c=core.connect()
 try:
  c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.pw(b['password']),b.get('display_name') or b['username']))
 except sqlite3.IntegrityError:return out({'error':'duplicate'},409)
 u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone();t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id']));c.close();return out({'user':core.user_json(u),'token':t},201)
async def login(r):
 b=await body(r);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username') if isinstance(b,dict) else '',core.pw(b.get('password','')) if isinstance(b,dict) else '')).fetchone()
 if not u:return out({'error':'unauthorized'},401)
 t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id']));return out({'user':core.user_json(u),'token':t})
async def me(r):
 u=auth(r);return out({'user':core.user_json(u)}) if u else out({'error':'unauthorized'},401)
async def health(r): return out({'status':'ok','node_id':r.app['node']})
async def workspaces(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.id DESC',(u['id'],)).fetchall();return out({'workspaces':[dict(x) for x in rows]})
async def create_ws(r):
 u=auth(r);b=await body(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect()
 try:c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']))
 except:return out({'error':'duplicate'},409)
 w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(w['id'],'general'));ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']));return out({'workspace':dict(w),'general_channel':core.chan_json(ch)},201)
async def ws_detail(r):
 u=auth(r);c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not u:return out({'error':'unauthorized'},401)
 if not w:return out({'error':'not found'},404)
 cs=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall();return out({'workspace':dict(w),'channels':[core.chan_json(x) for x in cs]})
async def create_channel(r):
 u=auth(r);b=await body(r);c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not u:return out({'error':'unauthorized'},401)
 if not w:return out({'error':'not found'},404)
 if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b.get('name'))).fetchone():return out({'error':'duplicate'},409)
 c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b.get('name'),int(bool(b.get('is_private'))),b.get('topic','')));ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']));return out({'channel':core.chan_json(ch)},201)
async def messages(r):
 u=auth(r);c=core.connect();ch=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
 if not u:return out({'error':'unauthorized'},401)
 if not ch:return out({'error':'not found'},404)
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():return out({'error':'forbidden'},403)
 if r.method=='POST':
  b=await body(r)
  if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return out({'error':'invalid'},400)
  text=b['body']
  role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone() if ch['workspace_id'] else None
  if text.startswith('//'):
   text=text[1:]
  elif text.startswith('/me ') and text[4:].strip():
   pass
  elif text == '/me' or text.startswith('/me '):
   return out({'error':'invalid'},400)
  elif text.startswith('/shrug'):
   rest=text[6:].strip()
   text=(rest+' ' if rest else '')+'¯\\_(ツ)_/¯'
  elif text.startswith('/topic '):
   topic=text[7:].strip()
   if not topic:return out({'error':'invalid'},400)
   c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']));ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
   return out({'message':None,'channel':core.chan_json(ch)},201)
  elif text.startswith('/invite '):
   target=text[8:].strip().lstrip('@')
   person=c.execute('SELECT * FROM users WHERE username=?',(target,)).fetchone()
   if not person:return out({'error':'not found'},404)
   c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],person['id']))
   return out({'message':None,'invited_user':core.user_json(person)},201)
  elif text in ('/archive','/unarchive'):
   if not role or role['role']!='owner':return out({'error':'forbidden'},403)
   archived=1 if text=='/archive' else 0
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(archived,ch['id']));ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
   return out({'message':None,'channel':core.chan_json(ch)},201)
  elif text.startswith('/'):
   return out({'error':'unknown command'},400)
  if ch['is_archived']:return out({'error':'archived'},423)
  fids=b.get('file_ids',[])
  if fids is not None and (not isinstance(fids,list) or any(not isinstance(x,int) for x in fids)): return out({'error':'invalid'},400)
  for fid in fids:
   f=c.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone()
   if not f:return out({'error':'invalid'},400)
  c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],text,b.get('parent_id'),core.now()))
  mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  for fid in fids:c.execute('UPDATE files SET message_id=? WHERE id=?',(mid,fid))
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  mentions=set()
  for uname in re.findall(r'(?<![\\w@])@([A-Za-z0-9_]+)',text):
   if uname in ('channel','here'): continue
   person=c.execute('SELECT u.id FROM users u JOIN members wm ON wm.user_id=u.id WHERE wm.workspace_id=? AND u.username=?',(ch['workspace_id'],uname)).fetchone()
   if person and person[0]!=u['id'] and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],person[0])).fetchone():mentions.add(person[0])
   gr=c.execute('SELECT g.id FROM groups g WHERE g.workspace_id=? AND g.handle=?',(ch['workspace_id'],uname)).fetchone()
   if gr:
    for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(gr[0],)).fetchall():
     if x[0]!=u['id'] and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],x[0])).fetchone():mentions.add(x[0])
  if '@channel' in text:
   mentions.update(x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],u['id'])).fetchall())
  if '@here' in text:
   fresh=time.time()-3
   mentions.update(x[0] for x in c.execute('SELECT DISTINCT p.user_id FROM presence p JOIN channel_members cm ON cm.user_id=p.user_id WHERE cm.channel_id=? AND p.user_id!=? AND p.last_seen>?',(ch['id'],u['id'],fresh)).fetchall())
  d=core.msg_json(m);d['mentions']=sorted(mentions)
  core.emit(c,ch['id'],'message.created',{'message':dict(m)});return out({'message':d},201)
 ms=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(ch['id'],)).fetchall();return out({'messages':[core.msg_json(x) for x in ms],'next_cursor':None})
async def channel_members(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect();ch=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
 if not ch:return out({'error':'not found'},404)
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():return out({'error':'forbidden'},403)
 rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall()
 return out({'members':[core.user_json(x) for x in rows]})
async def replies(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect();parent=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone()
 if not parent:return out({'error':'not found'},404)
 ch=c.execute('SELECT * FROM channels WHERE id=?',(parent['channel_id'],)).fetchone()
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():return out({'error':'forbidden'},403)
 rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(parent['id'],)).fetchall()
 return out({'replies':[core.msg_json(x) for x in rows],'next_cursor':None})
async def message_edit_delete(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone()
 if not m:return out({'error':'not found'},404)
 ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
 role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone() if ch['workspace_id'] else None
 owner=bool(role and role['role']=='owner')
 if r.method=='PATCH':
  if m['deleted']:return out({'error':'deleted'},409)
  if m['author_id']!=u['id']:return out({'error':'forbidden'},403)
  b=await body(r)
  if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return out({'error':'invalid'},400)
  edited=core.now();c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],edited,m['id']))
  m=c.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone()
  d=core.msg_json(m)
  mentions=set()
  for uname in re.findall(r'(?<![\\w@])@([A-Za-z0-9_]+)',b['body']):
   person=c.execute('SELECT u.id FROM users u JOIN members wm ON wm.user_id=u.id WHERE wm.workspace_id=? AND u.username=?',(ch['workspace_id'],uname)).fetchone()
   if person and person[0]!=u['id'] and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],person[0])).fetchone(): mentions.add(person[0])
  d['mentions']=sorted(mentions)
  frame=core.emit(c,ch['id'],'message.updated',{'message':dict(m)})
  return out({'message':d})
 if m['author_id']!=u['id'] and not owner:return out({'error':'forbidden'},403)
 if m['deleted']:return out({'deleted':True})
 c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m['id'],))
 core.emit(c,ch['id'],'message.deleted',{'message_id':m['id']})
 return out({'deleted':True})
async def get_channel_route(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect();ch=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
 if not ch:return out({'error':'not found'},404)
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():return out({'error':'forbidden'},403)
 return out({'channel':core.chan_json(ch)})
async def patch_channel(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect();ch=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
 if not ch:return out({'error':'not found'},404)
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():return out({'error':'forbidden'},403)
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return out({'error':'invalid'},400)
 c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']))
 ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
 return out({'channel':core.chan_json(ch)})
async def join(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect();ch=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
 if not ch:return out({'error':'not found'},404)
 c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
 if ch['workspace_id']: c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
 return out({'channel':core.chan_json(ch)})
async def reaction(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone()
 if not m:return out({'error':'not found'},404)
 ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():return out({'error':'forbidden'},403)
 emoji=r.match_info.get('emoji')
 if r.method=='POST':
  b=await body(r);emoji=b.get('emoji') if isinstance(b,dict) else None
  if not isinstance(emoji,str) or not emoji:return out({'error':'invalid'},400)
  old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],emoji)).fetchone()
  if not old:
   c.execute('INSERT INTO reactions VALUES(?,?,?,?)',(m['id'],u['id'],emoji,core.now()))
   core.emit(c,ch['id'],'reaction.added',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
 else:
  if isinstance(emoji,str):
   old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],emoji)).fetchone()
   if old:
    c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],emoji))
    core.emit(c,ch['id'],'reaction.removed',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
 rows=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(created_at)',(m['id'],)).fetchall()
 return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rows]})
async def wsock(r):
 u=auth(r)
 if not u:
  return out({'error':'unauthorized'},401)
 w=web.WebSocketResponse()
 await w.prepare(r)
 subscribed={}
 global_last=0
 conn_id=str(uuid.uuid4()); pc=core.connect(); pc.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],time.time())); pc.close()
 async def deliver():
  while not w.closed:
   try:
    nonlocal global_last
    c0=core.connect(); grows=c0.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_last,)).fetchall();c0.close()
    for gr in grows:
     await w.send_str(gr['payload']);global_last=gr['id']
    for cid,last in list(subscribed.items()):
     c=core.connect()
     rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
     c.close()
     for row in rows:
      await w.send_str(row['payload'])
      subscribed[cid]=row['event_id']
   except Exception:
    break
   await asyncio.sleep(0.05)
 task=asyncio.create_task(deliver())
 try:
  async for x in w:
   if x.type == WSMsgType.TEXT:
    try: b=json.loads(x.data)
    except: continue
    cid=b.get('channel_id')
    if b.get('type') == 'subscribe':
     c=core.connect();row=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone();c.close()
     head=row[0] if row else 0
     subscribed[cid]=head
     await w.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
    elif b.get('type') == 'resume':
     c=core.connect()
     since=int(b.get('since_event_id',0))
     rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall()
     row=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone();c.close()
     for ev in rows: await w.send_str(ev['payload'])
     head=row[0] if row else 0
     subscribed[cid]=head
     await w.send_json({'type':'resumed','channel_id':cid,'head_event_id':head})
 finally:
  task.cancel()
  pc=core.connect();pc.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,));pc.close()
 return w

async def dms(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('user_id'),int):return out({'error':'invalid'},400)
 c=core.connect();other=c.execute('SELECT * FROM users WHERE id=?',(b['user_id'],)).fetchone()
 if not other:return out({'error':'not found'},404)
 a,z=sorted((u['id'],other['id']))
 row=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,z)).fetchone()
 if row:return out({'channel_id':row[0]})
 c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)')
 cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
 c.execute('INSERT INTO dms VALUES(?,?,?)',(a,z,cid))
 c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,z))
 return out({'channel_id':cid})
async def files(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect()
 if r.method=='POST':
  rd=await r.multipart();part=await rd.next()
  if not part:return out({'error':'invalid'},400)
  data=await part.read(decode=False)
  if len(data)>10*1024*1024:return out({'error':'too large'},413)
  fn=part.filename or 'file';ct=part.headers.get('Content-Type','application/octet-stream')
  c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],fn,ct,len(data),core.now()))
  fid=c.execute('SELECT last_insert_rowid()').fetchone()[0];os.makedirs('/app/data/files',exist_ok=True)
  open('/app/data/files/'+str(fid),'wb').write(data)
  row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
  return out({'file':{k:row[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}},201)
 row=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone()
 if not row:return out({'error':'not found'},404)
 if r.path.endswith('/download'):
  return web.Response(body=open('/app/data/files/'+str(row['id']),'rb').read(),content_type=row['content_type'],headers={'Content-Disposition':'attachment; filename="'+row['filename']+'"'})
 return out({'file':{k:row[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}})
def group_obj(c,g):
 ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY rowid',(g['id'],)).fetchall()]
 return {'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids}
async def groups(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not w:return out({'error':'not found'},404)
 role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
 if not role:return out({'error':'forbidden'},403)
 handle=r.match_info.get('handle')
 if r.method=='GET':
  gs=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall()
  return out({'groups':[group_obj(c,g) for g in gs]})
 g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone() if handle else None
 if r.method=='POST':
  b=await body(r)
  if not isinstance(b,dict) or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9_-]{2,32}',b['handle']) or not isinstance(b.get('user_ids'),list):return out({'error':'invalid'},400)
  if c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(w['id'],b['handle'])).fetchone():return out({'error':'duplicate'},409)
  ids=b['user_ids']; valid={x[0] for x in c.execute('SELECT user_id FROM members WHERE workspace_id=?',(w['id'],)).fetchall()}
  if any(not isinstance(x,int) or x not in valid for x in ids):return out({'error':'invalid'},400)
  c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],b['handle'],b.get('name',''),u['id']));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  for uid in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,uid))
  return out({'group':group_obj(c,c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone())},201)
 if not g:
  if r.method=='DELETE': return out({'deleted':True})
  return out({'error':'not found'},404)
  if g['creator_id']!=u['id'] and role['role']!='owner':return out({'error':'forbidden'},403)
  c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups WHERE id=?',(g['id'],));return out({'deleted':True})
 if g['creator_id']!=u['id'] and role['role']!='owner':return out({'error':'forbidden'},403)
 b=await body(r);ids=b.get('user_ids') if isinstance(b,dict) else None
 if ids is not None:
  valid={x[0] for x in c.execute('SELECT user_id FROM members WHERE workspace_id=?',(w['id'],)).fetchall()}
  if not isinstance(ids,list) or any(x not in valid for x in ids):return out({'error':'invalid'},400)
  c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
  for uid in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],uid))
 if isinstance(b,dict) and isinstance(b.get('name'),str):c.execute('UPDATE groups SET name=? WHERE id=?',(b['name'],g['id']))
 return out({'group':group_obj(c,c.execute('SELECT * FROM groups WHERE id=?',(g['id'],)).fetchone())})
async def group_delete(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not w:return out({'error':'not found'},404)
 g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone()
 if not g:return out({'deleted':True})
 role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
 if not role:return out({'error':'forbidden'},403)
 if g['creator_id']!=u['id'] and role['role']!='owner':return out({'error':'forbidden'},403)
 c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups WHERE id=?',(g['id'],));return out({'deleted':True})
async def user_profile(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 c=core.connect(); target=c.execute('SELECT * FROM users WHERE id=?',(int(r.match_info['id']),)).fetchone()
 if not target:return out({'error':'not found'},404)
 return out({'user':core.user_json(target)})
async def profile_me(r):
 u=auth(r)
 if not u:return out({'error':'unauthorized'},401)
 b=await body(r)
 if not isinstance(b,dict):return out({'error':'invalid'},400)
 allowed={'display_name':80,'status_text':140,'status_emoji':32,'avatar_url':512}
 for k,v in b.items():
  if k not in allowed and k!='timezone':return out({'error':'invalid'},400)
  if k in allowed and (not isinstance(v,str) or not v or len(v)>allowed[k]):return out({'error':'invalid'},400)
 if 'timezone' in b:
  if not isinstance(b['timezone'],str) or b['timezone'] not in ('UTC','America/New_York','America/Los_Angeles','Europe/London'):return out({'error':'invalid'},400)
 c=core.connect()
 for k,v in b.items():c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
 row=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
 frame={'type':'user.updated','user':core.user_json(row)};c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(frame),))
 return out({'user':core.user_json(row)})
async def protected_stub(r):
 if not auth(r): return out({'error':'unauthorized'},401)
 return out({'error':'not found'},404)

async def root(r): return web.FileResponse('/app/static/index.html')
async def appjs(r): return web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'})
app=web.Application();app['node']=0
for path,method,fn in [('/api/health','GET',health),('/api/auth/register','POST',reg),('/api/auth/login','POST',login),('/api/auth/me','GET',me),('/api/workspaces','GET',workspaces),('/api/workspaces','POST',create_ws),('/api/workspaces/{slug}','GET',ws_detail),('/api/workspaces/{slug}/channels','POST',create_channel),('/api/channels/{id}/messages','GET',messages),('/api/channels/{id}/messages','POST',messages),('/api/messages/{id}/replies','GET',replies),('/api/messages/{id}','PATCH',message_edit_delete),('/api/messages/{id}','DELETE',message_edit_delete),('/api/dms','POST',dms),('/api/files','POST',files),('/api/files/{id}','GET',files),('/api/files/{id}/download','GET',files),('/api/workspaces/{slug}/groups','GET',groups),('/api/workspaces/{slug}/groups','POST',groups),('/api/workspaces/{slug}/groups/{handle}','PATCH',groups),('/api/workspaces/{slug}/groups/{handle}','DELETE',group_delete),('/api/users/{id}','GET',user_profile),('/api/users/me','PATCH',profile_me),('/api/channels/{id}','GET',get_channel_route),('/api/channels/{id}','PATCH',patch_channel),('/api/channels/{id}/members','GET',channel_members),('/api/channels/{id}/join','POST',join),('/api/messages/{id}/reactions','POST',reaction),('/api/messages/{id}/reactions/{emoji}','DELETE',reaction),('/api/ws','GET',wsock),('/app.js','GET',appjs),('/','GET',root)]:app.router.add_route(method,path,fn)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);a=p.parse_args();app['node']=a.node;web.run_app(app,host='127.0.0.1',port=8000+a.node)
