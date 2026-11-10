import secrets
import asyncio,json,argparse,re,os
from aiohttp import web
import core
NODE=0
def out(x,status=200): return web.json_response(x,status=status)
def err(s,m): return out({'error':m},s)
async def body(r):
 try: x=await r.json(); return x if isinstance(x,dict) else None
 except: return None
def user(r):
 h=r.headers.get('Authorization','')
 return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
def auth(r):
 u=user(r)
 if not u:
  t=r.query.get('token',''); u=core.user_for_token(t[7:] if t.startswith('Bearer ') else t)
 return u
def juser(r): return core.user_to_json(r)
def allowed(u,ch):
 c=core.connect()
 x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
 if not x and ch['workspace_id'] and not ch['is_private']:
  x=c.execute("SELECT 1 FROM members WHERE workspace_id=? AND user_id=? AND role!='guest'",(ch['workspace_id'],u['id'])).fetchone()
 c.close(); return bool(x)
async def health(r): return out({'status':'ok','node_id':NODE})
async def register(r):
 d=await body(r)
 if not d or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d['username']) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid registration')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
   uid=cur.lastrowid;t=core.issue_token(c,uid);u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
  return out({'user':juser(u),'token':t},201)
 except: return err(409,'username already exists')
async def login(r):
 d=await body(r); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if d else '',)).fetchone() if d else None
 if not u or not isinstance(d.get('password'),str) or not core.verify_password(d['password'],u['password']): c.close(); return err(401,'invalid credentials')
 t=core.issue_token(c,u['id']);c.close();return out({'user':juser(u),'token':t})
async def me(r):
 u=auth(r); return out({'user':juser(u)}) if u else err(401,'unauthorized')
async def workspaces(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); rows=c.execute('SELECT DISTINCT w.* FROM workspaces w LEFT JOIN members m ON m.workspace_id=w.id WHERE w.owner_id=? OR m.user_id=?',(u['id'],u['id'])).fetchall();c.close()
 return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 d=await body(r)
 if not d or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d['slug']) or not isinstance(d.get('name'),str):return err(400,'invalid workspace')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']));w=cur.lastrowid
   core.add_workspace_member(c,w,u['id'],'owner');g=core.create_channel_row(c,w,'general');core.add_channel_member(c,g,u['id'])
   wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone();ch=c.execute('SELECT * FROM channels WHERE id=?',(g,)).fetchone()
  return out({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(ch)},201)
 except:return err(409,'duplicate workspace')
def calc_read(u,ch,val):
 c=core.connect(); ev=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch['id'],val)).fetchall(); unread=mention=0
 for e in ev:
  try:
   z=json.loads(e['payload']); m=z.get('message',{})
   if m and m.get('author_id')!=u['id'] and z.get('type') in ('message.created','message.reply') and m.get('id'):
    unread+=1
    if u['id'] in (m.get('mentions') or []):mention+=1
  except: pass
 c.close();return {'channel_id':ch['id'],'last_read_event_id':val,'unread_count':unread,'mention_count':mention}

async def workspace(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT DISTINCT c.* FROM channels c LEFT JOIN channel_members cm ON cm.channel_id=c.id LEFT JOIN members wm ON wm.workspace_id=c.workspace_id AND wm.user_id=? WHERE c.workspace_id=? AND (c.is_archived=0 OR ? IN (\'1\',\'true\')) AND (c.is_private=0 AND wm.user_id IS NOT NULL OR cm.user_id=?)',(u['id'],w['id'],r.query.get('include_archived',''),u['id'])).fetchall();c.close()
 c2=core.connect(); states=[]
 for x in rows:
  q=c2.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone(); states.append(calc_read(u,x,q['last_read_event_id'] if q else 0))
 c2.close()
 return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':states})
async def channels(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 d=await body(r)
 if not d or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']):return err(400,'invalid channel')
 try:
  with core.tx() as c:
   role=core.member_role(w['id'],u['id'])
   if role not in ('owner','admin','member'):return err(403,'forbidden')
   private=d.get('is_private',False)
   if not isinstance(private,bool):return err(400,'invalid private')
   topic=d.get('topic','')
   if not isinstance(topic,str) or len(topic)>250:return err(400,'invalid topic')
   if private and role not in ('owner','admin'):return err(403,'forbidden')
   cid=core.create_channel_row(c,w['id'],d['name'],private,topic);core.add_channel_member(c,cid,u['id']);x=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  return out({'channel':core.channel_to_json(x)},201)
 except:return err(409,'duplicate channel')
async def join(r):
 u=auth(r); ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if ch['is_private'] and not allowed(u,ch): return err(403,'forbidden')
 role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
 if role=='guest': return err(403,'forbidden')
 with core.tx() as c:
  if ch['workspace_id'] and not role:
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
   if not w or w['join_mode']!='open': return err(403,'forbidden')
   core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
  core.add_channel_member(c,ch['id'],u['id'])
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def patch_workspace(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 d=await body(r)
 if not d or (('name' in d) and not isinstance(d['name'],str)) or (('join_mode' in d) and d['join_mode'] not in ('open','invite_only')):return err(400,'invalid workspace')
 with core.tx() as c:
  if 'name' in d:c.execute('UPDATE workspaces SET name=? WHERE id=?',(d['name'],w['id']))
  if 'join_mode' in d:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(d['join_mode'],w['id']))
 return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def messages(r):
 u=auth(r);ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not allowed(u,ch):return err(403,'forbidden')
 if r.method=='POST' and ch['is_archived'] and not (isinstance((await body(r)).get('body') if r.can_read_body else None,str) and (await body(r)).get('body','').strip().startswith('/unarchive')):return err(423,'channel archived')
 if r.method=='POST':
  d=await body(r)
  if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'empty body')
  raw=d['body']
  if raw.startswith('/') and not raw.startswith('//'):
   parts=raw.split(None,1);cmd=parts[0];arg=parts[1].strip() if len(parts)>1 else ''
   if cmd=='/me':
    if not arg:return err(400,'text required')
   elif cmd=='/shrug':
    d['body']=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
   elif cmd=='/topic':
    if not arg:return err(400,'topic required')
    if ch['is_archived']:return err(423,'channel archived')
    c0=core.connect();cm=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone();c0.close()
    if not cm and core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
    if len(arg)>250:return err(400,'invalid topic')
    with core.tx() as c:
     c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']));fresh=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone();core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(fresh)})
    return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
   elif cmd=='/invite':
    if not re.fullmatch(r'@[A-Za-z0-9_]+',arg):return err(400,'invalid invite')
    c0=core.connect();x=c0.execute('SELECT u.* FROM users u JOIN members m ON m.user_id=u.id WHERE u.username=? AND m.workspace_id=?',(arg[1:],ch['workspace_id'])).fetchone();c0.close()
    if not x:return err(400,'unknown user')
    with core.tx() as c:core.add_channel_member(c,ch['id'],x['id'])
    return out({'message':None,'invited_user':core.user_to_json(x)},201)
   elif cmd in ('/archive','/unarchive'):
    if core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
    active=cmd=='/archive'
    if bool(ch['is_archived'])!=active:
     with core.tx() as c:
      c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(active),ch['id']));fresh=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone();core.emit_event(c,ch['id'],'channel.archived' if active else 'channel.unarchived',{'channel':core.channel_to_json(fresh)})
    return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
   else:return err(400,'unknown command')
  elif raw.startswith('//'): d['body']=raw[1:]
  if d.get('parent_id') is not None and (not isinstance(d['parent_id'],int) or isinstance(d['parent_id'],bool)):return err(400,'invalid parent')
  msg=core.create_message(u,ch['id'],d['body'],d.get('parent_id'))
  fids=d.get('file_ids',[])
  if fids is not None and (not isinstance(fids,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in fids)):return err(400,'invalid file_ids')
  if fids:
   c=core.connect(); fs=c.execute('SELECT * FROM files WHERE id IN (%s)'%','.join('?'*len(fids)),fids).fetchall(); c.close()
   if len(fs)!=len(set(fids)) or any(x['uploader_id']!=u['id'] or x['message_id'] is not None for x in fs):return err(400,'invalid file')
   with core.tx() as c:
    c.executemany('UPDATE files SET message_id=? WHERE id=?',[(msg['id'],fid) for fid in fids])
   msg['files']=[core.file_to_json(x) for x in fs]
  return out({'message':msg},201)
 
 if 'page' in r.query or 'offset' in r.query:return err(400,'offset pagination unsupported')
 try:
  lim=int(r.query.get('limit','50'))
 except:return err(400,'invalid limit')
 if lim<1 or lim>200:return err(400,'invalid limit')
 import base64
 cur=r.query.get('cursor'); before=None
 if cur:
  try:
   raw=base64.urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)); z=json.loads(raw)
   if z.get('k')!='messages' or not isinstance(z.get('v'),int):raise ValueError()
   before=z['v']
  except:return err(400,'invalid cursor')
 c=core.connect()
 if before is None: rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT ?',(ch['id'],lim+1)).fetchall()
 else: rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL AND id<? ORDER BY id DESC LIMIT ?',(ch['id'],before,lim+1)).fetchall()
 c.close(); more=len(rows)>lim; rows=rows[:lim]
 nxt=None
 if more:nxt=base64.urlsafe_b64encode(json.dumps({'k':'messages','v':rows[-1]['id']}).encode()).decode().rstrip('=')
 return out({'messages':[core.message_json(x) for x in rows],'next_cursor':nxt})

async def members(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT m.user_id,m.role,u.* FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
 return out({'members':[{'user_id':x['user_id'],'role':x['role'],'user':juser(x)} for x in rows]})
async def patch_role(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 target=int(r.match_info['user_id']); role=core.member_role(w['id'],u['id']); d=await body(r)
 if not core.member_role(w['id'],target):return err(404,'not found')
 if role not in ('owner','admin'):return err(403,'forbidden')
 if not d or d.get('role') not in ('admin','member','guest'):return err(400,'invalid role')
 old=core.member_role(w['id'],target)
 if target==w['owner_id'] or (role=='admin' and old=='admin'):return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(d['role'],w['id'],target))
 return out({'member':{'user_id':target,'role':d['role']}})
async def transfer(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if u['id']!=w['owner_id']:return err(403,'forbidden')
 d=await body(r)
 if not d or not isinstance(d.get('new_owner_id'),int) or isinstance(d.get('new_owner_id'),bool):return err(400,'invalid target')
 target=d['new_owner_id']
 if core.member_role(w['id'],target)!='admin':return err(400,'target must be admin')
 with core.tx() as c:
  c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
  c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id']))
  c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],target))
 return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def invite_create(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 d=await body(r) or {}; import time
 code=__import__('secrets').token_urlsafe(12); mx=d.get('max_uses',1)
 if not isinstance(mx,int) or isinstance(mx,bool) or mx<1:return err(400,'invalid max_uses')
 target=d.get('invited_user_id')
 if target is not None and (not isinstance(target,int) or isinstance(target,bool)):return err(400,'invalid target')
 if d.get('invited_username') is not None:
  if not isinstance(d['invited_username'],str):return err(400,'invalid target')
  q=core.connect(); ur=q.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone(); q.close()
  if not ur:return err(400,'unknown user')
  target=ur['id']
 secs=d.get('expires_in_seconds',604800)
 if not isinstance(secs,(int,float)) or isinstance(secs,bool) or secs<=0:return err(400,'invalid expiration')
 exp=time.time()+secs
 if target is not None: mx=1
 with core.tx() as c:c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?)',(code,w['id'],target,exp,mx,0,0))
 return out({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':mx,'uses':0}},201)
async def invite_list(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 import time;c=core.connect(); rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND expires_at>? AND uses<max_uses',(w['id'],time.time())).fetchall();c.close()
 return out({'invitations':[dict(x) for x in rows]})
async def invite_delete(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 code=r.match_info['code'];c=core.connect();x=c.execute('SELECT * FROM invitations WHERE code=? AND workspace_id=? AND revoked=0',(code,w['id'])).fetchone();c.close()
 if not x:return err(404,'not found')
 with core.tx() as c:c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(code,))
 return out({'deleted':True})
async def invite_accept(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 code=r.match_info['code'];import time
 c=core.connect();x=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone();c.close()
 if not x or x['revoked'] or x['uses']>=x['max_uses'] or x['expires_at']<=time.time():return err(404,'not found')
 if x['invited_user_id'] and x['invited_user_id']!=u['id']:return err(403,'not invited')
 with core.tx() as c:
  core.add_workspace_member(c,x['workspace_id'],u['id'],'member');c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,))
 c2=core.connect(); ww=c2.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone(); c2.close()
 return out({'workspace':core.workspace_to_json(ww)})

def group_json(c,g):
 ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY rowid',(g['id'],)).fetchall()]
 return {'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids}
async def groups(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if not role:return err(403,'forbidden')
 c=core.connect()
 if r.method=='GET':
  gs=c.execute('SELECT * FROM groups WHERE workspace_id=? ORDER BY id',(w['id'],)).fetchall();outv={'groups':[group_json(c,g) for g in gs]};c.close();return out(outv)
 d=await body(r)
 if not d or not isinstance(d.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['handle']) or not isinstance(d.get('name'),str) or not isinstance(d.get('user_ids',[]),list):c.close();return err(400,'invalid group')
 ids=d.get('user_ids',[])
 if any(not isinstance(x,int) or isinstance(x,bool) or not core.member_role(w['id'],x) for x in ids):c.close();return err(400,'invalid member')
 try:
  with core.tx() as c:
   cur=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],d['handle'],d['name'],u['id']));gid=cur.lastrowid
   c.executemany('INSERT INTO group_members VALUES(?,?)',[(gid,x) for x in ids])
   g=c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone();res=group_json(c,g)
  return out({'group':res},201)
 except:return err(409,'duplicate group')
async def group_edit(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 c=core.connect();g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone();c.close()
 if not g:
  if r.method=='DELETE' and core.member_role(w['id'],u['id']): return out({'deleted':True})
  return err(404,'not found')
 if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
 if r.method=='DELETE':
  with core.tx() as c:c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups WHERE id=?',(g['id'],))
  return out({'deleted':True})
 d=await body(r)
 if not d:return err(400,'invalid group')
 with core.tx() as c:
  if 'name' in d:
   if not isinstance(d['name'],str):return err(400,'invalid name')
   c.execute('UPDATE groups SET name=? WHERE id=?',(d['name'],g['id']))
  if 'user_ids' in d:
   if not isinstance(d['user_ids'],list) or any(not isinstance(x,int) or not core.member_role(w['id'],x) for x in d['user_ids']):return err(400,'invalid members')
   c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.executemany('INSERT INTO group_members VALUES(?,?)',[(g['id'],x) for x in d['user_ids']])
  fresh=c.execute('SELECT * FROM groups WHERE id=?',(g['id'],)).fetchone();res=group_json(c,fresh)
 return out({'group':res})

async def dms(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 d=await body(r)
 if not d or not isinstance(d.get('user_id'),int) or isinstance(d.get('user_id'),bool):return err(400,'invalid user_id')
 c=core.connect();x=c.execute('SELECT id FROM users WHERE id=?',(d['user_id'],)).fetchone();c.close()
 if not x:return err(404,'not found')
 return out({'channel_id':core.open_dm(u,d['user_id'])})
async def files(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 if r.method=='POST':
  try:
   form=await r.multipart();part=await form.next()
   if not part or part.name!='file':return err(400,'missing file')
   data=await part.read()
   if len(data)>10485760:return err(413,'file too large')
   fid=core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)
   c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
   return out({'file':core.file_to_json(x)},201)
  except:return err(400,'invalid upload')
 fid=int(r.match_info['id']);c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
 if not x:return err(404,'not found')
 if r.path.endswith('/download'):
  return web.FileResponse('/app/data/files/'+str(fid),headers={'Content-Type':x['content_type'],'Content-Disposition':'attachment; filename="'+x['filename']+'"'})
 return out({'file':core.file_to_json(x)})

async def profile(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 if r.method=='GET':
  try:uid=int(r.match_info['id'])
  except:return err(404,'not found')
  c=core.connect();x=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close()
  return out({'user':juser(x)}) if x else err(404,'not found')
 d=await body(r)
 if not d:return err(400,'invalid profile')
 allowed_fields={'display_name':(str,1,80),'timezone':(str,1,999),'avatar_url':(str,0,512),'status_text':(str,0,140),'status_emoji':(str,0,32)}
 from zoneinfo import ZoneInfo
 for k,v in d.items():
  if k not in allowed_fields or not isinstance(v,str) or len(v)<allowed_fields[k][1] or len(v)>allowed_fields[k][2]:return err(400,'invalid profile')
  if k=='timezone':
   try:ZoneInfo(v)
   except:return err(400,'invalid timezone')
 with core.tx() as c:
  for k,v in d.items():c.execute('UPDATE users SET %s=? WHERE id=?'%(k),(v,u['id']))
  x=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone();core.emit_global(c,{'type':'user.updated','user':juser(x)})
 return out({'user':juser(x)})

async def missing_c_route(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 if r.path=='/api/dms':return err(404,'not found')
 return err(404,'not found')

async def edit_message(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 mid=int(r.match_info['id']);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 if m['deleted']:return err(409,'message deleted')
 if m['author_id']!=u['id']:return err(403,'forbidden')
 d=await body(r)
 if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'invalid body')
 with core.tx() as c:
  ids=[]
  ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
  for name in re.findall(r'(?<![\w@.])@(\w+)',d['body']):
   x=c.execute('SELECT u.id FROM users u JOIN members mm ON mm.user_id=u.id WHERE u.username=? AND mm.workspace_id=? AND u.id!=?',(name,ch['workspace_id'],u['id'])).fetchone()
   if x:ids.append(x['id'])
  ids=list(dict.fromkeys(ids));c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(d['body'],core.now_iso(),json.dumps(ids),mid));row=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();core.emit_event(c,m['channel_id'],'message.updated',{'message':core.message_json(row)})
 return out({'message':core.message_json(row)})
async def delete_message(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 mid=int(r.match_info['id']);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch=core.get_channel(m['channel_id']);owner=core.member_role(ch['workspace_id'],u['id'])=='owner' if ch['workspace_id'] else False
 if m['author_id']!=u['id'] and not owner:return err(403,'forbidden')
 if m['deleted']:return out({'deleted':True})
 with core.tx() as c:
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
 return out({'deleted':True})

async def reactions(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 mid=int(r.match_info['id']);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch=core.get_channel(m['channel_id'])
 if not allowed(u,ch):return err(403,'forbidden')
 if ch['is_archived']:return err(423,'channel archived')
 if r.method=='POST':
  d=await body(r)
  if not d or not isinstance(d.get('emoji'),str) or not d['emoji']:return err(400,'invalid emoji')
  return out({'reactions':core.react(u,mid,d['emoji'],True)})
 return out({'reactions':core.react(u,mid,r.match_info['emoji'],False)})

async def read_state(r):
 u=auth(r); ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not allowed(u,ch):return err(403,'forbidden')
 d=await body(r) if r.method=='POST' else {}
 if r.method=='POST' and (not d or not isinstance(d.get('last_read_event_id'),int) or isinstance(d.get('last_read_event_id'),bool) or d['last_read_event_id']<0):return err(400,'invalid read state')
 c=core.connect()
 old=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()
 val=old['last_read_event_id'] if old else 0
 if r.method=='POST':
  val=max(val,d['last_read_event_id'])
  c.execute('INSERT INTO read_state(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],ch['id'],val))
 c.close()
 return out({'read_state':calc_read(u,ch,val)})

async def pins(r):
 u=auth(r);ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not allowed(u,ch):return err(403,'forbidden')
 import base64
 try: lim=int(r.query.get('limit','50'))
 except: return err(400,'invalid limit')
 if lim<1 or lim>200:return err(400,'invalid limit')
 before=None
 if r.query.get('cursor'):
  try:
   z=json.loads(base64.urlsafe_b64decode(r.query['cursor']+'='*((4-len(r.query['cursor'])%4)%4)))
   if z.get('k')!='pins' or not isinstance(z.get('v'),int):raise ValueError()
   before=z['v']
  except:return err(400,'invalid cursor')
 c=core.connect()
 q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'
 args=[ch['id']]
 if before is not None:q+=' AND p.message_id<?';args.append(before)
 q+=' ORDER BY p.message_id DESC LIMIT ?';args.append(lim+1)
 rows=c.execute(q,args).fetchall();c.close();more=len(rows)>lim;rows=rows[:lim];nxt=None
 if more:nxt=base64.urlsafe_b64encode(json.dumps({'k':'pins','v':rows[-1]['message_id']}).encode()).decode().rstrip('=')
 return out({'pins':[{'message':core.message_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':nxt})

async def search(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 q=r.query.get('q','')
 if not q:return err(400,'missing q')
 try: lim=int(r.query.get('limit','50'))
 except:return err(400,'invalid limit')
 if lim<1 or lim>200 or 'page' in r.query or 'offset' in r.query:return err(400,'invalid pagination')
 import base64
 before=None; cur=r.query.get('cursor')
 if cur:
  try:
   z=json.loads(base64.urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)))
   if z.get('k')!='search' or not isinstance(z.get('v'),int): raise ValueError()
   before=z['v']
  except:return err(400,'invalid cursor')
 c=core.connect()
 sql="SELECT DISTINCT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=? LEFT JOIN members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=? WHERE m.deleted=0 AND lower(m.body) LIKE ? AND (cm.user_id IS NOT NULL OR (ch.is_private=0 AND wm.user_id IS NOT NULL AND wm.role!='guest'))"
 args=[u['id'],u['id'],'%'+q.lower()+'%']
 if r.query.get('workspace_id'):
  try: sql+=' AND ch.workspace_id=?';args.append(int(r.query['workspace_id']))
  except:return err(400,'invalid workspace')
 if before is not None:sql+=' AND m.id<?';args.append(before)
 sql+=' ORDER BY m.id DESC LIMIT ?';args.append(lim+1)
 rows=c.execute(sql,args).fetchall();c.close();more=len(rows)>lim;rows=rows[:lim]
 nxt=None
 if more:nxt=base64.urlsafe_b64encode(json.dumps({'k':'search','v':rows[-1]['id']}).encode()).decode().rstrip('=')
 return out({'results':[core.message_json(x) for x in rows],'next_cursor':nxt})

async def replies(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();parent=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(r.match_info['id']),)).fetchone();c.close()
 if not parent:return err(404,'not found')
 ch=core.get_channel(parent['channel_id'])
 if not allowed(u,ch):return err(403,'forbidden')
 import base64
 try:lim=int(r.query.get('limit','50'))
 except:return err(400,'invalid limit')
 if lim<1 or lim>200:return err(400,'invalid limit')
 before=None
 if r.query.get('cursor'):
  try:
   z=json.loads(base64.urlsafe_b64decode(r.query['cursor']+'='*((4-len(r.query['cursor'])%4)%4)))
   if z.get('k')!='replies' or not isinstance(z.get('v'),int):raise ValueError()
   before=z['v']
  except:return err(400,'invalid cursor')
 c=core.connect();q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';args=[parent['id']]
 if before is not None:q+=' AND id>?';args.append(before)
 q+=' ORDER BY id ASC LIMIT ?';args.append(lim+1)
 rows=c.execute(q,args).fetchall();c.close();more=len(rows)>lim;rows=rows[:lim];nxt=None
 if more:nxt=base64.urlsafe_b64encode(json.dumps({'k':'replies','v':rows[-1]['id']}).encode()).decode().rstrip('=')
 return out({'replies':[core.message_json(x) for x in rows],'next_cursor':nxt})

async def pin(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(r.match_info['id']),)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch=core.get_channel(m['channel_id'])
 if not allowed(u,ch):return err(403,'forbidden')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(m['id'],u['id'],core.now_iso()))
  x=c.execute('SELECT * FROM pins WHERE message_id=?',(m['id'],)).fetchone()
 return out({'pin':{'message_id':x['message_id'],'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}})
async def unpin(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 if not m:return err(404,'not found')
 with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(m['id'],))
 return out({'unpinned':True})

async def leave(r):
 u=auth(r);ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 with core.tx() as c:
  gone=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).rowcount
  if gone: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
 return out({'channel':core.channel_to_json(ch)})
async def archive(r):
 u=auth(r);ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
 active=r.match_info.get('action')=='archive'
 with core.tx() as c:
  if bool(ch['is_archived']) != active:
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(active),ch['id']))
   fresh=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
   core.emit_event(c,ch['id'],'channel.archived' if active else 'channel.unarchived',{'channel':core.channel_to_json(fresh)})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})

async def patch_channel(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 d=await body(r)
 if not d or not isinstance(d.get('topic'),str) or len(d['topic'])>250:return err(400,'invalid topic')
 if ch['is_archived']:return err(423,'channel archived')
 c0=core.connect(); cm=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
 if not cm and core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],ch['id']))
  fresh=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
  core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(fresh)})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def channel_members(r):
 u=auth(r);ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not allowed(u,ch):return err(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
 return out({'members':[juser(x) for x in rows]})

async def ws(r):
 u=auth(r)
 if not u:return web.Response(status=401)
 conn_id=secrets.token_urlsafe(12); pc=core.connect(); pc.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],__import__('time').time())); ge=pc.execute('SELECT COALESCE(MAX(id),0) n FROM global_events').fetchone()['n']; pc.close(); w=web.WebSocketResponse();await w.prepare(r); subs={}; replaying=set(); alive=True; presence_channels=set(); global_last=ge
 async def poll():
  nonlocal global_last
  while alive:
   gc=core.connect()
   gev=gc.execute('SELECT * FROM global_events WHERE id>? ORDER BY id',(global_last,)).fetchall()
   gc.close()
   for e in gev:
    await w.send_str(e['payload'])
    global_last=e['id']
   for cid,last in list(subs.items()):
    if cid in replaying: continue
    pc=core.connect(); pc.execute('UPDATE presence SET last_seen=? WHERE conn_id=?',(__import__('time').time(),conn_id)); pc.close()
    c=core.connect(); ev=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall();c.close()
    for e in ev: await w.send_str(e['payload']);subs[cid]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for m in w:
   if m.type==1:
    d=json.loads(m.data);cid=int(d.get('channel_id',0))
    if d.get('type')=='subscribe':
     head=core.get_channel(cid)['last_event_id']; subs[cid]=head; presence_channels.add(cid); pc=core.connect(); pc.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],__import__('time').time())); pc.execute('INSERT OR IGNORE INTO presence_channels VALUES(?,?)',(conn_id,cid)); pc.close(); await w.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
    elif d.get('type')=='resume':
     since=int(d.get('since_event_id',0)); replaying.add(cid); subs[cid]=since
     c=core.connect(); ev=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); c.close()
     for e in ev:
      await w.send_str(e['payload']); subs[cid]=e['event_id']
     head=core.get_channel(cid)['last_event_id']; subs[cid]=head; replaying.discard(cid)
     await w.send_json({'type':'resumed','channel_id':cid,'head_event_id':head})
 finally:
  alive=False;task.cancel(); c0=core.connect(); c0.execute('DELETE FROM presence_channels WHERE conn_id=?',(conn_id,)); c0.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,)); c0.close()
 return w
async def root(r): return web.FileResponse('/app/static/index.html')
def app():
 a=web.Application();a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_patch('/api/workspaces/{slug}',patch_workspace);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer);a.router.add_post('/api/workspaces/{slug}/invitations',invite_create);a.router.add_get('/api/workspaces/{slug}/invitations',invite_list);a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',invite_delete);a.router.add_post('/api/invitations/{code}/accept',invite_accept);a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',patch_role);a.router.add_post('/api/workspaces/{slug}/channels',channels);a.router.add_route('*','/api/channels/{id}/messages',messages);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_get('/api/channels/{id}/pins',pins);a.router.add_get('/api/search',search);a.router.add_route('*','/api/channels/{id}/read',read_state);a.router.add_post('/api/messages/{id}/pin',pin);a.router.add_patch('/api/messages/{id}',edit_message);a.router.add_delete('/api/messages/{id}',delete_message);a.router.add_post('/api/messages/{id}/reactions',reactions);a.router.add_post('/api/dms',dms);a.router.add_route('*','/api/workspaces/{slug}/groups',groups);a.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',group_edit);a.router.add_get('/api/users/{id}',profile);a.router.add_patch('/api/users/me',profile);a.router.add_post('/api/files',files);a.router.add_get('/api/files/{id}',files);a.router.add_get('/api/files/{id}/download',files);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reactions);a.router.add_delete('/api/messages/{id}/pin',unpin);a.router.add_post('/api/channels/{id}/join',join);a.router.add_post('/api/channels/{id}/leave',leave);a.router.add_post('/api/channels/{id}/{action:archive|unarchive}',archive);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_get('/api/ws',ws);a.router.add_get('/',root);a.router.add_static('/','/app/static',show_index=False);return a
if __name__=='__main__':
 core.init_db();p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;web.run_app(app(),host='127.0.0.1',port=8000+NODE)
