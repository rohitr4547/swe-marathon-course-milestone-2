import asyncio,json,re,os,sys,base64,time,uuid
from aiohttp import web,WSMsgType
import core
core.init_db()
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
sockets=[]
def out(x,status=200): return web.json_response(x,status=status)
def err(s,m): return out({'error':m},s)
async def body(r):
 try:return await r.json()
 except:return None
def user(r):
 h=r.headers.get('Authorization','')
 return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
async def auth(r):
 u=user(r)
 return u or None
def members(cid,uid):
 c=core.connect();x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone();c.close();return bool(x)
def channel_access(cid,uid):
 ch=core.get_channel(cid)
 if not ch:return None,404
 if not members(cid,uid):return ch,403
 return ch,0
def msgjson(r):
 c=core.connect(); author=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(r['id'],)).fetchall()
 fs=c.execute('SELECT * FROM files WHERE message_id=? ORDER BY id',(r['id'],)).fetchall()
 cm=c.execute('SELECT u.id,u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(r['channel_id'],)).fetchall()
 ids={x['id'] for x in cm if x['id']!=r['author_id']}; text=r['body'] or ''; mentions=set()
 for x in cm:
  if x['id']!=r['author_id'] and re.search(r'(?<![A-Za-z0-9_])@'+re.escape(x['username'])+r'\b',text): mentions.add(x['id'])
 if re.search(r'(?<![A-Za-z0-9_])@channel\b',text): mentions.update(ids)
 if re.search(r'(?<![A-Za-z0-9_])@here\b',text):
  pres=c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall(); mentions.update(x['user_id'] for x in pres if x['user_id'] in ids)
 w=c.execute('SELECT workspace_id FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
 if w:
  gs=c.execute('SELECT g.handle,gm.user_id FROM groups_ g JOIN group_members gm ON gm.group_id=g.id WHERE g.workspace_id=?',(w['workspace_id'],)).fetchall()
  for g in gs:
   if re.search(r'(?<![A-Za-z0-9_])@'+re.escape(g['handle'])+r'\b',text) and g['user_id'] in ids: mentions.add(g['user_id'])
 c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':core.user_to_json(author),'body':r['body'],'parent_id':r['parent_id'],'reply_count':0,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[filejson(x) for x in fs],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs],'mentions':list(mentions)}
async def health(r):return out({'status':'ok','node_id':NODE})
async def register(r):
 d=await body(r)
 if not isinstance(d,dict) or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
   uid=c.execute('SELECT last_insert_rowid()').fetchone()[0];t=core.issue_token(c,uid);u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
 except: return err(409,'duplicate')
 return out({'user':core.user_to_json(u),'token':t},201)
async def login(r):
 d=await body(r); c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if isinstance(d,dict) else '',)).fetchone();c.close()
 if not u or not isinstance(d,dict) or not core.verify_password(d.get('password',''),u['password']):return err(401,'invalid')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=await auth(r)
 return out({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspaces(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return out({'workspaces':[dict(x) for x in rows]})
async def create_ws(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 d=await body(r)
 if not isinstance(d,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))):return err(400,'invalid')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d.get('name',''),u['id']));wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
 except:return err(409,'duplicate')
 return out({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()) if False else {'id':wid,'slug':d['slug'],'name':d.get('name',''),'owner_id':u['id'],'join_mode':'open'},'general_channel':core.channel_to_json(core.get_channel(cid))},201)
async def get_ws(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not w:return err(404,'not found')
 ch=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 return out({'workspace':dict(w),'channels':[core.channel_to_json(x) for x in ch],'read_state':{}})
async def create_channel(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 d=await body(r);c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
 if not w:return err(404,'not found')
 if not members(c.execute('SELECT id FROM channels WHERE workspace_id=? LIMIT 1',(w['id'],)).fetchone()['id'],u['id']):return err(403,'forbidden')
 if not isinstance(d,dict) or not re.fullmatch(r'[a-z0-9-]{1,32}',str(d.get('name',''))):return err(400,'invalid')
 try:
  with core.tx() as x:x.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic','')));cid=x.execute('SELECT last_insert_rowid()').fetchone()[0];x.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
 except:return err(409,'duplicate')
 return out({'channel':core.channel_to_json(core.get_channel(cid))},201)
async def join(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(r.match_info['id'])
 if not ch:return err(404,'not found')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
  if ch['workspace_id'] and not ch['is_private']:
   c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
 return out({'channel':core.channel_to_json(ch)})
async def postmsg(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 cid=int(r.match_info['id']);ch,st=channel_access(cid,u['id'])
 if st:return err(st,'not found' if st==404 else 'forbidden')
 if ch['is_archived'] and not (isinstance(await body(r),dict) and isinstance((await body(r)).get('body'),str) and (await body(r)).get('body','').strip()=='/unarchive'): return err(423,'archived')
 d=await body(r)
 if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'invalid')
 text=d['body']
 if text.startswith('//'): text=text[1:]
 elif text.startswith('/'):
  parts=text.split(' ',1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
  if cmd=='/me':
   if not arg.strip(): return err(400,'invalid')
  elif cmd=='/shrug': text=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
  elif cmd=='/topic':
   if not arg.strip(): return err(400,'invalid')
   with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']))
   return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
  elif cmd=='/invite':
   if not re.fullmatch(r'@[A-Za-z0-9_]+',arg.strip()): return err(400,'invalid')
   c=core.connect();target=c.execute('SELECT * FROM users WHERE username=?',(arg.strip()[1:],)).fetchone();c.close()
   if not target:return err(400,'invalid')
   with core.tx() as c:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],target['id']))
   return out({'message':None,'invited_user':core.user_to_json(target)},201)
  elif cmd in ('/archive','/unarchive'):
   c=core.connect();w=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
   if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
   with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if cmd=='/archive' else 0,ch['id']))
   return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
  else:return err(400,'unknown command')
 d['body']=text
 fids=d.get('file_ids',[])
 if fids is None:fids=[]
 if not isinstance(fids,list) or any(not isinstance(x,int) for x in fids):return err(400,'invalid')
 with core.tx() as c:
  for fid in fids:
   f=c.execute('SELECT uploader_id,message_id FROM files WHERE id=?',(fid,)).fetchone()
   if not f or f['uploader_id']!=u['id'] or f['message_id'] is not None:return err(400,'invalid')
  c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],d['body'],d.get('parent_id'),core.now_iso()));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  for fid in fids:c.execute('UPDATE files SET message_id=? WHERE id=?',(mid,fid))
  r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();core.emit_event(c,cid,'message.reply' if d.get('parent_id') else 'message.created',{'message':msgjson(r)})
 return out({'message':msgjson(r)},201)
async def channel_members(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 ch,st=channel_access(int(r.match_info['id']),u['id'])
 if st:return err(st,'not found' if st==404 else 'forbidden')
 c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=? ORDER BY u.id',(ch['id'],)).fetchall();c.close()
 return out({'members':[core.user_to_json(x) for x in rows]})

async def listmsg(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 ch,st=channel_access(int(r.match_info['id']),u['id'])
 if st:return err(st,'not found' if st==404 else 'forbidden')
 c=core.connect();rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(ch['id'],)).fetchall();c.close();return out({'messages':[msgjson(x) for x in rs],'next_cursor':None})
async def react(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch,st=channel_access(m['channel_id'],u['id'])
 if st:return err(st,'not found' if st==404 else 'forbidden')
 if ch['is_archived']:return err(423,'archived')
 d=await body(r);e=d.get('emoji') if isinstance(d,dict) else None
 if not isinstance(e,str) or not e:return err(400,'invalid')
 with core.tx() as c:
  cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?,?)',(m['id'],u['id'],e,core.now_iso()))
  if cur.rowcount: core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':m['id'],'emoji':e,'user_id':u['id']})
 c=core.connect()
 rows=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(m['id'],)).fetchall()
 c.close()
 return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rows]})
async def archive(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(r.match_info['id'])
 if not ch:return err(404,'not found')
 c=core.connect();w=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
 if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE channels SET is_archived=1 WHERE id=?',(ch['id'],))
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def unarchive(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(r.match_info['id'])
 if not ch:return err(404,'not found')
 c=core.connect();w=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
 if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE channels SET is_archived=0 WHERE id=?',(ch['id'],))
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})


def filejson(r):
 return {'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}
async def dms(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 d=await body(r); oid=d.get('user_id') if isinstance(d,dict) else None
 if not isinstance(oid,int):return err(400,'invalid')
 c=core.connect(); other=c.execute('SELECT id FROM users WHERE id=?',(oid,)).fetchone();c.close()
 if not other:return err(404,'not found')
 a,b=sorted((u['id'],oid))
 with core.tx() as c:
  x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
  if x: cid=x['channel_id']
  else:
   c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)');cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,b))
 return out({'channel_id':cid})
async def upload(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 try:
  reader=await r.multipart()
  f=await reader.next()
 except: return err(400,'file required')
 if not f or not getattr(f,'filename',None): return err(400,'file required')
 data=await f.read()
 if len(data)>10*1024*1024:return err(413,'too large')
 with core.tx() as c:
  c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],f.filename or 'file',f.headers.get('Content-Type','application/octet-stream'),len(data),core.now_iso()))
  fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
 os.makedirs('/app/data/files',exist_ok=True);open('/app/data/files/'+str(fid),'wb').write(data)
 c=core.connect();row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
 return out({'file':filejson(row)},201)
async def getfile(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 return err(404,'not found') if not x else out({'file':filejson(x)})
async def download(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not x:return err(404,'not found')
 return web.Response(body=open('/app/data/files/'+str(x['id']),'rb').read(),content_type=x['content_type'],headers={'Content-Disposition':'attachment; filename="'+x['filename']+'"'})


def group_json(g):
 c=core.connect(); ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id',(g['id'],)).fetchall()];c.close()
 return {'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids}
def ws_and_role(slug,uid):
 c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();m=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone() if w else None;c.close();return w,m['role'] if m else None
async def groups(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 w,role=ws_and_role(r.match_info['slug'],u['id'])
 if not w:return err(404,'not found')
 if not role:return err(403,'forbidden')
 c=core.connect();gs=c.execute('SELECT * FROM groups_ WHERE workspace_id=? ORDER BY id',(w['id'],)).fetchall();c.close()
 if r.method=='GET':return out({'groups':[group_json(x) for x in gs]})
 d=await body(r)
 if not isinstance(d,dict) or not re.fullmatch(r'[a-z0-9-]{1,32}',str(d.get('handle',''))) or not isinstance(d.get('name'),str) or not isinstance(d.get('user_ids'),list):return err(400,'invalid')
 c=core.connect()
 if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],d['handle'])).fetchone():c.close();return err(409,'duplicate')
 good=all(isinstance(x,int) and c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone() for x in d['user_ids']);c.close()
 if not good:return err(400,'invalid')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],d['handle'],d['name'],u['id']));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   for x in d['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,x))
 except:return err(409,'duplicate')
 return out({'group':group_json(core.connect().execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone())},201)
async def group_item(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 w,role=ws_and_role(r.match_info['slug'],u['id'])
 if not w:return err(404,'not found')
 c=core.connect();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone();c.close()
 if not g:
  return out({'deleted':True}) if r.method=='DELETE' else err(404,'not found')
 if role not in ('owner',) and g['creator_id']!=u['id']:return err(403,'forbidden')
 if r.method=='DELETE':
  with core.tx() as c:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
  return out({'deleted':True})
 d=await body(r)
 if not isinstance(d,dict):return err(400,'invalid')
 with core.tx() as c:
  if 'name' in d and isinstance(d['name'],str):c.execute('UPDATE groups_ SET name=? WHERE id=?',(d['name'],g['id']))
  if 'user_ids' in d and isinstance(d['user_ids'],list):
   c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
   for x in d['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],x))
 return out({'group':group_json(core.connect().execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone())})


async def profile(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM users WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 return err(404,'not found') if not x else out({'user':core.user_to_json(x)})
async def profile_me(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 d=await body(r); allowed={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512),'timezone':(1,128)}
 if not isinstance(d,dict) or any(k not in allowed or not isinstance(v,str) or len(v)<allowed[k][0] or len(v)>allowed[k][1] for k,v in d.items()):return err(400,'invalid')
 if 'timezone' in d:
  try: from zoneinfo import ZoneInfo;ZoneInfo(d['timezone'])
  except: return err(400,'invalid')
 with core.tx() as c:
  for k,v in d.items():c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
  fresh=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
  core.emit_global(c,{'type':'user.updated','user':core.user_to_json(fresh)})
 c=core.connect();x=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone();c.close()
 return out({'user':core.user_to_json(x)})

async def api_ws(r):
 tok=r.query.get('token')
 u=core.user_for_token(tok) if tok else await auth(r)
 if not u:return err(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(r);conn_id=str(uuid.uuid4());core.touch_presence(conn_id,u['id']) if hasattr(core,'touch_presence') else None;last={}
 gc=core.connect(); grow=gc.execute('SELECT COALESCE(MAX(id),0) n FROM global_events').fetchone(); gseen=grow['n']; gc.close()
 async def global_poller():
  nonlocal gseen
  while True:
   await asyncio.sleep(.05)
   for gid,g in core.globals_after(gseen):
    await ws.send_json(g); gseen=gid
 gp=asyncio.create_task(global_poller())
 try:
  async for z in ws:
   if z.type==WSMsgType.TEXT:
    d=json.loads(z.data)
    if d.get('type') in ('subscribe','resume'):
     cid=int(d['channel_id']); head=core.get_channel(cid)['last_event_id']; last[cid]=head if d['type']=='subscribe' else int(d.get('since_event_id',0)); await ws.send_json({'type':'subscribed' if d['type']=='subscribe' else 'resumed','channel_id':cid,'head_event_id':head})
     while True:
      await asyncio.sleep(.05)
      for gid,g in core.globals_after(gseen):
       await ws.send_json(g); gseen=gid
      es=core.events_after(cid,last[cid])
      for e in es:await ws.send_json(e);last[cid]=e['event_id']
 finally:
  gp.cancel()
  core.drop_presence(conn_id) if hasattr(core,'drop_presence') else None
 return ws
async def root(r):return web.Response(text='<html><body><h1>Huddle</h1></body></html>',content_type='text/html')
async def unreact(r):
 u=await auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch,st=channel_access(m['channel_id'],u['id'])
 if st:return err(st,'not found' if st==404 else 'forbidden')
 if ch['is_archived']:return err(423,'archived')
 emoji=r.match_info.get('emoji','')
 with core.tx() as c:
  cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],emoji))
  if cur.rowcount: core.emit_event(c,m['channel_id'],'reaction.removed',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
 c=core.connect();rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(m['id'],)).fetchall();c.close()
 return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs]})

app=web.Application(client_max_size=11*1024*1024)
app.router.add_get('/api/health',health);app.router.add_get('/api/users/{id}',profile);app.router.add_patch('/api/users/me',profile_me);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_ws);app.router.add_get('/api/workspaces/{slug}',get_ws);app.router.add_post('/api/workspaces/{slug}/channels',create_channel);app.router.add_route('*','/api/workspaces/{slug}/groups',groups);app.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',group_item);app.router.add_post('/api/dms',dms);app.router.add_post('/api/files',upload);app.router.add_get('/api/files/{id}',getfile);app.router.add_get('/api/files/{id}/download',download)
app.router.add_post('/api/channels/{id}/join',join);app.router.add_get('/api/channels/{id}/members',channel_members);app.router.add_post('/api/channels/{id}/archive',archive);app.router.add_post('/api/channels/{id}/unarchive',unarchive);app.router.add_post('/api/channels/{id}/messages',postmsg);app.router.add_get('/api/channels/{id}/messages',listmsg)
app.router.add_post('/api/messages/{id}/reactions',react);app.router.add_get('/api/ws',api_ws);app.router.add_get('/',root)
app.router.add_delete('/api/messages/{id}/reactions/{emoji}',unreact)
web.run_app(app,host='127.0.0.1',port=8000+NODE)
