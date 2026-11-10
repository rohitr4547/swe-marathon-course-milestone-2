import argparse,json,re,asyncio,time,secrets
from zoneinfo import ZoneInfo
from aiohttp import web,WSMsgType
import core
NODE=0
def json_error(s,m): return web.json_response({'error':m},status=s)
async def read_json(r):
 try:
  x=await r.json();return x if isinstance(x,dict) else None
 except:return None
def require_user(r):
 h=r.headers.get('Authorization','')
 if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
 u=core.user_for_token(h[7:])
 if not u: raise web.HTTPUnauthorized()
 return u
@web.middleware
async def json_middleware(request, handler):
 try:
  return await handler(request)
 except web.HTTPException as e:
  return web.json_response({'error': e.reason or 'error'}, status=e.status)
def health(r): return web.json_response({'status':'ok','node_id':NODE})
async def register(r):
 d=await read_json(r)
 if not d or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d['username']) or not isinstance(d.get('password'),str) or len(d['password'])<8:return json_error(400,'invalid registration')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
   u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone();t=core.issue_token(c,u['id'])
  return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
 except Exception:return json_error(409,'username exists')
async def login(r):
 d=await read_json(r);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if d else '',)).fetchone();c.close()
 if not u or not isinstance(d.get('password') if d else None,str) or not core.verify_password(d['password'],u['password']):return json_error(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.user_to_json(u),'token':t})
async def me(r): return web.json_response({'user':core.user_to_json(require_user(r))})
async def workspaces(r):
 u=require_user(r);c=core.connect();rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(r):
 u=require_user(r);d=await read_json(r)
 if not d or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d['slug']) or not isinstance(d.get('name'),str):return json_error(400,'invalid workspace')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']));wid=cur.lastrowid
   c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'));cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));ch=c.execute('SELECT * FROM channels WHERE id=?',(cur.lastrowid,)).fetchone();w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
  return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},status=201)
 except:return json_error(409,'duplicate workspace')
async def workspace(r):
 u=require_user(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return json_error(404,'not found')
 if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
 c=core.connect();rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],r.query.get('include_archived') in ('1','true'))).fetchall();rows=[x for x in rows if not x['is_private'] or core._cm(x['id'],u['id']) or w['owner_id']==u['id']];states=[]
 for x in rows:
  rr=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone()
  last=rr[0] if rr else 0
  unread=c.execute("SELECT count(*) FROM events e JOIN messages m ON json_extract(e.payload, '$.message.id')=m.id WHERE e.channel_id=? AND e.event_id>? AND m.author_id!=?",(x['id'],last,u['id'])).fetchone()[0]
  states.append({'channel_id':x['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':c.execute("SELECT count(*) FROM events e JOIN messages m ON json_extract(e.payload, '$.message.id')=m.id WHERE e.channel_id=? AND e.event_id>? AND m.author_id!=? AND json_array_length(m.mentions)>0",(x['id'],last,u['id'])).fetchone()[0]})
 c.close();return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':states})
async def channels(r):
 u=require_user(r);w=core.get_workspace(r.match_info['slug']);d=await read_json(r)
 if not w:return json_error(404,'not found')
 if not d or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']) or (not isinstance(d.get('topic',''),str)) or len(d.get('topic',''))>250:return json_error(400,'invalid channel')
 role=core.member_role(w['id'],u['id'])
 if role not in ('owner','admin','member') or (role=='member' and bool(d.get('is_private'))):return json_error(403,'forbidden')
 c0=core.connect(); dup=c0.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone(); c0.close()
 if dup:return json_error(409,'duplicate channel')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic','')));ch=c.execute('SELECT * FROM channels WHERE id=?',(cur.lastrowid,)).fetchone();core.add_channel_member(c,ch['id'],u['id'])
  return web.json_response({'channel':core.channel_to_json(ch)},status=201)
 except:return json_error(409,'duplicate channel')
async def join(r):
 u=require_user(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return json_error(404,'not found')
 if ch['is_private'] and (not ch['workspace_id'] or not core._cm(ch['id'],u['id'])):return json_error(403,'forbidden')
 if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):
  ww=core.connect().execute('SELECT join_mode FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
  if ww and ww['join_mode']=='invite_only':return json_error(403,'forbidden')
 if ch['workspace_id'] and core.member_role(ch['workspace_id'],u['id'])=='guest':return json_error(403,'forbidden')
 with core.tx() as c:
  if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):core.add_workspace_member(c,ch['workspace_id'],u['id'])
  core.add_channel_member(c,ch['id'],u['id'])
 return web.json_response({'channel':core.channel_to_json(ch)})
async def msgs(r):
 u=require_user(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return json_error(404,'not found')
 if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):return json_error(403,'forbidden')
 if ch['is_private'] and not core._cm(ch['id'],u['id']):return json_error(403,'forbidden')
 if r.method=='GET':
  if r.query.get('offset') is not None or r.query.get('page') is not None:return json_error(400,'offset pagination unsupported')
  try: limit=int(r.query.get('limit','50'))
  except:return json_error(400,'invalid limit')
  if limit<1 or limit>200:return json_error(400,'invalid limit')
  allm=core.list_messages(ch['id']); cur=r.query.get('cursor'); start=0
  if cur:
   try:
    raw=__import__('base64').urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)); q=json.loads(raw); 
    if q.get('k')!='messages' or not isinstance(q.get('v'),int):raise ValueError()
    start=next((i for i,x in enumerate(allm) if x['id']<q['v']),len(allm))
   except:return json_error(400,'invalid cursor')
  page=allm[start:start+limit]; nxt=None
  if start+limit<len(allm):
   nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'messages','v':page[-1]['id']}).encode()).decode().rstrip('=')
  return web.json_response({'messages':page,'next_cursor':nxt})
 d=await read_json(r)
 if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'empty body')
 if ch['is_archived'] and d['body']!='/unarchive':return json_error(423,'channel archived')
 if d.get('file_ids') is not None:
  if not isinstance(d.get('file_ids'),list) or any(isinstance(x,bool) or not isinstance(x,int) for x in d['file_ids']):return json_error(400,'invalid file_ids')
  cc=core.connect()
  for fid in d['file_ids']:
   ff=cc.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone()
   if not ff:cc.close();return json_error(400,'invalid file')
  cc.close()
 if d.get('parent_id') is not None:
  if not isinstance(d.get('parent_id'),int):return json_error(400,'invalid parent_id')
  try:
   cc=core.connect(); parent=cc.execute('SELECT * FROM messages WHERE id=? AND channel_id=? AND parent_id IS NULL AND deleted=0',(int(d['parent_id']),ch['id'])).fetchone();cc.close()
  except: parent=None
  if not parent:return json_error(404,'parent not found')
 body=d['body']
 if body.startswith('//'): d['body']=body[1:]
 elif body.startswith('/me'):
  if body=='/me' or not body[3:].strip():return json_error(400,'command text required')
 elif body.startswith('/shrug'):
  text=body[6:].strip();d['body']=(text+' ' if text else '')+'¯\\_(ツ)_/¯'
 elif body.startswith('/topic'):
  text=body[6:].strip()
  if not text or len(text)>250:return json_error(400,'invalid topic')
  if not core._cm(ch['id'],u['id']):return json_error(403,'forbidden')
  with core.tx() as c:
   c.execute('UPDATE channels SET topic=? WHERE id=?',(text,ch['id']))
   core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
  return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
 elif body.startswith('/invite'):
  name=body[7:].strip().lstrip('@')
  if not name:return json_error(400,'invalid invite')
  cc=core.connect();target=cc.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone();cc.close()
  if not target:return json_error(404,'user not found')
  with core.tx() as c:core.add_channel_member(c,ch['id'],target['id'])
  return web.json_response({'message':None,'invited_user':core.user_to_json(target)},status=201)
 elif body in ('/archive','/unarchive'):
  w=None
  if ch['workspace_id']:
   cc=core.connect();w=cc.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();cc.close()
  if not w or w['owner_id']!=u['id']:return json_error(403,'forbidden')
  val=1 if body=='/archive' else 0
  with core.tx() as c:
   if bool(ch['is_archived'])!=bool(val):
    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
    core.emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
  return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
 elif body.startswith('/'):
  return json_error(400,'unknown command')
 d['body']=d['body']
 msg=core.create_message(u,ch['id'],d['body'],d.get('parent_id'))
 if d.get('file_ids'):
  with core.tx() as c:
   for fid in d['file_ids']:c.execute('UPDATE files SET message_id=? WHERE id=?',(msg['id'],fid))
  c=core.connect();fs=c.execute('SELECT * FROM files WHERE message_id=?',(msg['id'],)).fetchall();c.close()
  msg['files']=[dict(x) for x in fs]
 return web.json_response({'message':msg},status=201)
async def channel_patch(r):
 u=require_user(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return json_error(404,'not found')
 if ch['is_archived']:return json_error(423,'channel archived')
 d=await read_json(r)
 if not d or not isinstance(d.get('topic'),str) or len(d['topic'])>250:return json_error(400,'invalid topic')
 if not core._cm(ch['id'],u['id']) and (not ch['workspace_id'] or core.connect().execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()['owner_id']!=u['id']):return json_error(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],ch['id']))
  core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
 return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def channel_members(r):
 u=require_user(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return json_error(404,'not found')
 c=core.connect();rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
 return web.json_response({'members':[core.user_to_json(x) for x in rows]})
async def channel_leave(r):
 u=require_user(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return json_error(404,'not found')
 with core.tx() as c:core.remove_channel_member(c,ch['id'],u['id'])
 return web.json_response({'channel':core.channel_to_json(ch)})
async def channel_archive(r):
 u=require_user(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return json_error(404,'not found')
 w=None
 if ch['workspace_id']:
  cc=core.connect();w=cc.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();cc.close()
 if not w or w['owner_id']!=u['id']:return json_error(403,'forbidden')
 archived=0 if r.match_info.get('id') and r.path.endswith('/unarchive') else 1
 with core.tx() as c:
  if bool(ch['is_archived']) != bool(archived):
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(archived,ch['id']))
   core.emit_event(c,ch['id'],'channel.archived' if archived else 'channel.unarchived',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
 return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def pin(r):
 u=require_user(r); mid=int(r.match_info['id']); c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone();c.close()
 if not m:return json_error(404,'not found')
 ch=core.get_channel(m['channel_id'])
 if ch['workspace_id'] and (not core.member_role(ch['workspace_id'],u['id']) or (ch['is_private'] and not core._cm(ch['id'],u['id']))):return json_error(403,'forbidden')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()));x=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
 return web.json_response({'pin':{'message_id':x['message_id'],'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}})
async def unpin(r):
 u=require_user(r);mid=int(r.match_info['id'])
 with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
 return web.json_response({'unpinned':True})
async def pinlist(r):
 u=require_user(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return json_error(404,'not found')
 if ch['workspace_id'] and (not core.member_role(ch['workspace_id'],u['id']) or (ch['is_private'] and not core._cm(ch['id'],u['id']))):return json_error(403,'forbidden')
 try: limit=int(r.query.get('limit','50'))
 except:return json_error(400,'invalid limit')
 if limit<1 or limit>200:return json_error(400,'invalid limit')
 c=core.connect();rows=c.execute('SELECT m.*,p.message_id AS pin_id,p.pinned_by,p.pinned_at FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.message_id DESC',(ch['id'],)).fetchall();c.close()
 cur=r.query.get('cursor');start=0
 if cur:
  try:
   q=json.loads(__import__('base64').urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)))
   if q.get('k')!='pins' or not isinstance(q.get('v'),int):raise ValueError
   start=next((i for i,x in enumerate(rows) if x['pin_id']<q['v']),len(rows))
  except:return json_error(400,'invalid cursor')
 page=rows[start:start+limit];nxt=None
 if start+limit<len(rows):nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'pins','v':page[-1]['pin_id']}).encode()).decode().rstrip('=')
 return web.json_response({'pins':[{'message':core.message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in page],'next_cursor':nxt})
async def search(r):
 u=require_user(r);q=r.query.get('q')
 if not q:return json_error(400,'missing q')
 try:limit=int(r.query.get('limit','50'))
 except:return json_error(400,'invalid limit')
 if limit<1 or limit>200:return json_error(400,'invalid limit')
 c=core.connect();rows=c.execute('SELECT m.*,ch.workspace_id,ch.is_private FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.deleted=0 AND lower(m.body) LIKE ? ORDER BY m.id DESC',('%'+q.lower()+'%',)).fetchall();c.close()
 rows=[x for x in rows if (not x['workspace_id'] or (core.member_role(x['workspace_id'],u['id']) and (not x['is_private'] or core._cm(x['channel_id'],u['id']))))]
 if r.query.get('workspace_id'):
  try:rows=[x for x in rows if x['workspace_id']==int(r.query['workspace_id'])]
  except:return json_error(400,'invalid workspace_id')
 cur=r.query.get('cursor');start=0
 if cur:
  try:
   z=json.loads(__import__('base64').urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)))
   if z.get('k')!='search' or not isinstance(z.get('v'),int):raise ValueError
   start=next((i for i,x in enumerate(rows) if x['id']<z['v']),len(rows))
  except:return json_error(400,'invalid cursor')
 page=rows[start:start+limit];nxt=None
 if start+limit<len(rows):nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'search','v':page[-1]['id']}).encode()).decode().rstrip('=')
 return web.json_response({'results':[core.message_to_json(x) for x in page],'next_cursor':nxt})
async def replies(r):
 u=require_user(r)
 c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(r.match_info['id']),)).fetchone(); c.close()
 if not m:return json_error(404,'not found')
 ch=core.get_channel(m['channel_id'])
 if ch['workspace_id'] and (not core.member_role(ch['workspace_id'],u['id']) or (ch['is_private'] and not core._cm(ch['id'],u['id']))):return json_error(403,'forbidden')
 if r.query.get('offset') is not None or r.query.get('page') is not None:return json_error(400,'offset pagination unsupported')
 try: limit=int(r.query.get('limit','50'))
 except:return json_error(400,'invalid limit')
 if limit<1 or limit>200:return json_error(400,'invalid limit')
 arr=list(reversed(core.list_messages(m['channel_id'],m['id'])));cur=r.query.get('cursor');start=0
 if cur:
  try:
   raw=__import__('base64').urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4));q=json.loads(raw)
   if q.get('k')!='replies' or not isinstance(q.get('v'),int):raise ValueError()
   start=next((i for i,x in enumerate(arr) if x['id']>q['v']),len(arr))
  except:return json_error(400,'invalid cursor')
 page=arr[start:start+limit];nxt=None
 if start+limit<len(arr):nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'replies','v':page[-1]['id']}).encode()).decode().rstrip('=')
 return web.json_response({'replies':page,'next_cursor':nxt})
async def read_state(r):
 u=require_user(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return json_error(404,'not found')
 if ch['workspace_id'] and (not core.member_role(ch['workspace_id'],u['id']) or (ch['is_private'] and not core._cm(ch['id'],u['id']))):return json_error(403,'forbidden')
 c=core.connect();x=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone();last=x[0] if x else 0
 unread=c.execute("SELECT count(*) FROM events e JOIN messages m ON json_extract(e.payload, '$.message.id')=m.id WHERE e.channel_id=? AND e.event_id>? AND m.author_id!=?",(ch['id'],last,u['id'])).fetchone()[0]
 obj={'channel_id':ch['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':c.execute("SELECT count(*) FROM events e JOIN messages m ON json_extract(e.payload, '$.message.id')=m.id WHERE e.channel_id=? AND e.event_id>? AND m.author_id!=? AND json_array_length(m.mentions)>0",(ch['id'],last,u['id'])).fetchone()[0]}
 if r.method=='POST':
  d=await read_json(r)
  if not d or isinstance(d.get('last_read_event_id'),bool) or not isinstance(d.get('last_read_event_id'),int):return json_error(400,'invalid event id')
  last=max(last,d['last_read_event_id'])
  with core.tx() as tc:tc.execute('INSERT INTO read_states(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],ch['id'],last))
  obj['last_read_event_id']=last
  c2=core.connect()
  obj['unread_count']=c2.execute("SELECT count(*) FROM events e JOIN messages m ON json_extract(e.payload, '$.message.id')=m.id WHERE e.channel_id=? AND e.event_id>? AND m.author_id!=?",(ch['id'],last,u['id'])).fetchone()[0]
  obj['mention_count']=c2.execute("SELECT count(*) FROM events e JOIN messages m ON json_extract(e.payload, '$.message.id')=m.id WHERE e.channel_id=? AND e.event_id>? AND m.author_id!=? AND json_array_length(m.mentions)>0",(ch['id'],last,u['id'])).fetchone()[0]
  c2.close()
 return web.json_response({'read_state':obj})

async def reaction(r):
 u=require_user(r);mid=int(r.match_info['id']);m=core.connect().execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
 if not m:return json_error(404,'not found')
 ch=core.get_channel(m['channel_id'])
 if ch['workspace_id'] and (not core.member_role(ch['workspace_id'],u['id']) or (ch['is_private'] and not core._cm(ch['id'],u['id']))):return json_error(403,'forbidden')
 d=await read_json(r);emoji=(d.get('emoji') if d else None) if r.method=='POST' else __import__('urllib.parse').parse.unquote(r.match_info.get('emoji',''))
 if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
 return web.json_response({'reactions':core.react(u,mid,emoji,r.method=='POST')})
async def dms(r):
 u=require_user(r);d=await read_json(r)
 if not d or isinstance(d.get('user_id'),bool) or not isinstance(d.get('user_id'),int):return json_error(400,'invalid user_id')
 c=core.connect();other=c.execute('SELECT * FROM users WHERE id=?',(d['user_id'],)).fetchone()
 if not other:c.close();return json_error(404,'user not found')
 a,b=sorted((u['id'],other['id']));x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone();c.close()
 if x:return web.json_response({'channel_id':x[0]})
 with core.tx() as c:
  c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)');cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,b))
 return web.json_response({'channel_id':cid})
async def file_upload(r):
 u=require_user(r);data=await r.post();f=data.get('file')
 if not f:return json_error(400,'file required')
 raw=f.file.read()
 if len(raw)>10*1024*1024:return json_error(413,'file too large')
 with core.tx() as c:
  cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],f.filename,f.content_type,len(raw),core.now_iso()));fid=cur.lastrowid
 open('/app/data/files/'+str(fid),'wb').write(raw)
 return web.json_response({'file':{'id':fid,'uploader_id':u['id'],'filename':f.filename,'content_type':f.content_type,'size':len(raw),'created_at':core.now_iso()}},status=201)
async def file_get(r):
 u=require_user(r);c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 if not x:return json_error(404,'not found')
 return web.json_response({'file':dict(x)})
async def file_download(r):
 u=require_user(r);c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 if not x:return json_error(404,'not found')
 return web.Response(body=open('/app/data/files/'+str(x['id']),'rb').read(),content_type=x['content_type'],headers={'Content-Disposition':'attachment; filename="'+x['filename']+'"'})

async def groups(r):
 u=require_user(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return json_error(404,'not found')
 if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
 c=core.connect();rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 if r.method=='GET':return web.json_response({'groups':[core.group_to_json(x) for x in rows]})
 d=await read_json(r);h=d.get('handle') if d else None;ids=d.get('user_ids',[]) if d else []
 if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9-]+',h) or not isinstance(ids,list):return json_error(400,'invalid group')
 if any(not isinstance(x,int) or not core.member_role(w['id'],x) for x in ids):return json_error(400,'invalid members')
 if any(x['handle']==h for x in rows):return json_error(409,'duplicate handle')
 return web.json_response({'group':core.create_group(w['id'],u['id'],h,d.get('name',''),ids)},status=201)
async def group_one(r):
 u=require_user(r);w=core.get_workspace(r.match_info['slug']);h=r.match_info['handle']
 if not w:return json_error(404,'not found')
 c=core.connect();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone();c.close()
 if not g:
  return web.json_response({'deleted':True}) if r.method=='DELETE' else json_error(404,'not found')
 if w['owner_id']!=u['id'] and g['creator_id']!=u['id']:return json_error(403,'forbidden')
 if r.method=='DELETE':
  with core.tx() as c:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],))
  return web.json_response({'deleted':True})
 d=await read_json(r)
 if not d:return json_error(400,'invalid group')
 ids=json.loads(g['user_ids'] or '[]') if 'user_ids' not in d else d.get('user_ids')
 if not isinstance(ids,list) or any(isinstance(x,bool) or not isinstance(x,int) or not core.member_role(w['id'],x) for x in ids):return json_error(400,'invalid members')
 with core.tx() as c:c.execute('UPDATE groups_ SET name=?,user_ids=? WHERE id=?',(d.get('name',g['name']),json.dumps(ids),g['id']))
 cc=core.connect();updated=cc.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone();cc.close()
 return web.json_response({'group':core.group_to_json(updated)})

async def user_get(r):
 u=require_user(r);c=core.connect();x=c.execute('SELECT * FROM users WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 if not x:return json_error(404,'not found')
 return web.json_response({'user':core.user_to_json(x)})
async def user_patch(r):
 u=require_user(r);d=await read_json(r)
 if not d:return json_error(400,'invalid profile')
 fields={}
 limits={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512)}
 for k,(lo,hi) in limits.items():
  if k in d:
   if not isinstance(d[k],str) or len(d[k])<lo or len(d[k])>hi:return json_error(400,'invalid profile')
   fields[k]=d[k]
 if 'timezone' in d:
  if not isinstance(d['timezone'],str):return json_error(400,'invalid timezone')
  try: ZoneInfo(d['timezone'])
  except: return json_error(400,'invalid timezone')
  fields['timezone']=d['timezone']
 with core.tx() as c:
  if fields:c.execute('UPDATE users SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',tuple(fields.values())+(u['id'],))
  x=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone();core.emit_global(c,{'type':'user.updated','user':core.user_to_json(x)})
 return web.json_response({'user':core.user_to_json(x)})

async def message_edit(r):
 u=require_user(r);mid=int(r.match_info['id']);d=await read_json(r)
 if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'invalid body')
 code,msg=core.edit_message(u,mid,d['body'])
 if code!='200':return json_error(int(code),'message '+code)
 return web.json_response({'message':msg})
async def message_delete(r):
 u=require_user(r);code,msg=core.delete_message(u,int(r.match_info['id']))
 if code!='200':return json_error(int(code),'message '+code)
 return web.json_response(msg)

async def c_placeholder(r):
 require_user(r);return json_error(404,'not found')

async def workspace_members(r):
 u=require_user(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return json_error(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if not role:return json_error(403,'forbidden')
 c=core.connect();rows=c.execute('SELECT m.*,u.* FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
 return web.json_response({'members':[{'user_id':x['user_id'],'role':x['role'],'user':core.user_to_json(x)} for x in rows]})
async def member_role_patch(r):
 u=require_user(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return json_error(404,'not found')
 caller=core.member_role(w['id'],u['id'])
 if caller not in ('owner','admin'):return json_error(403,'forbidden')
 try:target=int(r.match_info['user_id'])
 except:return json_error(404,'not found')
 tr=core.member_role(w['id'],target)
 if not tr:return json_error(404,'not found')
 d=await read_json(r);role=d.get('role') if d else None
 if not isinstance(role,str) or role not in ('admin','member','guest'):return json_error(400,'invalid role')
 if tr=='owner' or (caller=='admin' and tr=='admin'):return json_error(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target))
 return web.json_response({'member':{'user_id':target,'role':role}})
async def transfer(r):
 u=require_user(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return json_error(404,'not found')
 if w['owner_id']!=u['id']:return json_error(403,'forbidden')
 d=await read_json(r);target=d.get('new_owner_id') if d else None
 if isinstance(target,bool) or not isinstance(target,int) or core.member_role(w['id'],target)!='admin':return json_error(400,'invalid target')
 with core.tx() as c:
  c.execute('UPDATE memberships SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id']))
  c.execute('UPDATE memberships SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],target))
  c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
 return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})

async def workspace_patch(r):
 u=require_user(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return json_error(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if role not in ('owner','admin'):return json_error(403,'forbidden')
 d=await read_json(r)
 if not d:return json_error(400,'invalid body')
 if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join_mode')
 vals=[];sets=[]
 for k in ('name','join_mode'):
  if k in d:
   if not isinstance(d[k],str):return json_error(400,'invalid body')
   sets.append(k+'=?');vals.append(d[k])
 if sets:
  with core.tx() as c:c.execute('UPDATE workspaces SET '+','.join(sets)+' WHERE id=?',tuple(vals)+(w['id'],))
 return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def invitations(r):
 u=require_user(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return json_error(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if role not in ('owner','admin'):return json_error(403,'forbidden')
 c=core.connect()
 if r.method=='GET':
  import time
  rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND (expires IS NULL OR expires>?) AND uses<max_uses',(w['id'],time.time())).fetchall();c.close()
  return web.json_response({'invitations':[dict(x) for x in rows]})
 if r.method=='DELETE':
  code=r.match_info.get('code')
  x=c.execute('SELECT 1 FROM invitations WHERE workspace_id=? AND code=?',(w['id'],code)).fetchone();c.close()
  if not x:return json_error(404,'not found')
  with core.tx() as tc:tc.execute('DELETE FROM invitations WHERE code=?',(code,))
  return web.json_response({'deleted':True})
 d=await read_json(r)
 if d is None:return json_error(400,'invalid body')
 mu=d.get('max_uses',1);ex=d.get('expires_in_seconds',None)
 if isinstance(mu,bool) or not isinstance(mu,int) or mu<1 or (ex is not None and (isinstance(ex,bool) or not isinstance(ex,(int,float)) or ex<=0)):return json_error(400,'invalid invitation')
 target=d.get('invited_user_id')
 if target is not None and (isinstance(target,bool) or not isinstance(target,int)):return json_error(400,'invalid target')
 if d.get('invited_username') is not None:
  if not isinstance(d['invited_username'],str):return json_error(400,'invalid target')
  q=c.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone()
  if not q:return json_error(404,'user not found')
  target=q[0]
 import time,secrets
 if target is not None:mu=1
 code=secrets.token_urlsafe(12)
 with core.tx() as tc:tc.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,expires,max_uses) VALUES(?,?,?,?,?)',(code,w['id'],target,(time.time()+ex if ex is not None else None),mu))
 return web.json_response({'invitation':{'code':code,'workspace_id':w['id'],'invited_user_id':target,'max_uses':mu}},status=201)
async def invitation_accept(r):
 u=require_user(r);code=r.match_info['code'];import time
 c=core.connect();x=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone();c.close()
 if not x or (x['expires'] is not None and x['expires']<=time.time()) or x['uses']>=x['max_uses']:return json_error(404,'not found')
 if core.member_role(x['workspace_id'],u['id']):return web.json_response({'workspace':core.workspace_to_json(core.connect().execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone())})
 if x['invited_user_id'] is not None and x['invited_user_id']!=u['id']:return json_error(403,'forbidden')
 with core.tx() as tc:
  core.add_workspace_member(tc,x['workspace_id'],u['id']);tc.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,))
 return web.json_response({'workspace':core.workspace_to_json(core.connect().execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone())})

async def protected_placeholder(r):
 require_user(r)
 return json_error(404,'not found')

async def ws_handler(r):
 token=r.query.get('token')
 if token:
  if token.startswith('Bearer '): token=token[7:]
  u=core.user_for_token(token)
  if not u:return json_error(401,'unauthorized')
 else:
  try:u=require_user(r)
  except web.HTTPUnauthorized:return json_error(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(r);subs={};gc=core.connect();gr=[gc.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]];gc.close();conn_id=secrets.token_urlsafe(12);pc=core.connect();pc.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],time.time()));pc.close()
 async def send():
  while not ws.closed:
   gc=core.connect(); grows=gc.execute('SELECT payload FROM global_events WHERE id>? ORDER BY id',(gr[0],)).fetchall();gc.close()
   for gx in grows:
    await ws.send_json(json.loads(gx[0]))
   if grows: gr[0] += len(grows)
   for ch,n in list(subs.items()):
    for e in core.events_after(ch,n):await ws.send_json(e);subs[ch]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(send())
 try:
  async for m in ws:
   if m.type==WSMsgType.TEXT:
    d=json.loads(m.data);ch=int(d.get('channel_id'))
    if d['type']=='subscribe':subs[ch]=core.head_event_id(ch);await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':subs[ch]})
    elif d['type']=='resume':
     since=int(d.get('since_event_id',0)); subs[ch]=since
     sent=since
     for e in core.events_after(ch,since):
      await ws.send_json(e);sent=e['event_id'];subs[ch]=sent
     await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':sent})
 finally:
  task.cancel()
  pc=core.connect();pc.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,));pc.close()
 return ws
def app():
 a=web.Application(middlewares=[json_middleware]);a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_patch('/api/users/me',user_patch);a.router.add_get('/api/users/{id}',user_get);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace);a.router.add_post('/api/dms',dms);a.router.add_post('/api/files',file_upload);a.router.add_get('/api/files/{id}',file_get);a.router.add_get('/api/files/{id}/download',file_download);a.router.add_get('/api/users/{id}',c_placeholder);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_route('*','/api/workspaces/{slug}/groups',groups);a.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',group_one);a.router.add_get('/api/search',search);a.router.add_get('/api/channels/{id}/pins',pinlist);a.router.add_get('/api/channels/{id}/read',read_state);a.router.add_post('/api/channels/{id}/read',read_state);a.router.add_get('/api/workspaces/{slug}/members',workspace_members);a.router.add_patch('/api/workspaces/{slug}',workspace_patch);a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',member_role_patch);a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer);a.router.add_route('*','/api/workspaces/{slug}/invitations/{code}',invitations);a.router.add_route('*','/api/workspaces/{slug}/invitations',invitations);a.router.add_post('/api/invitations/{code}/accept',invitation_accept);a.router.add_post('/api/workspaces/{slug}/channels',channels);a.router.add_post('/api/channels/{id}/join',join);a.router.add_patch('/api/channels/{id}',channel_patch);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_post('/api/channels/{id}/leave',channel_leave);a.router.add_post('/api/channels/{id}/archive',channel_archive);a.router.add_post('/api/channels/{id}/unarchive',channel_archive);a.router.add_route('*','/api/channels/{id}/messages',msgs);a.router.add_route('*','/api/messages/{id}/replies',replies);a.router.add_post('/api/messages/{id}/pin',pin);a.router.add_delete('/api/messages/{id}/pin',unpin);a.router.add_post('/api/messages/{id}/reactions',reaction);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction);a.router.add_patch('/api/messages/{id}',message_edit);a.router.add_delete('/api/messages/{id}',message_delete);a.router.add_get('/api/ws',ws_handler);a.router.add_get('/app.js',lambda r:web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'}));a.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'));return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(app(),host='127.0.0.1',port=8000+NODE)
