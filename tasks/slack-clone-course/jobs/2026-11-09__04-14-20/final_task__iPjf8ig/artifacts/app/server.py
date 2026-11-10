import sys,json,re,asyncio
from aiohttp import web
import core
core.init_db()
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
def out(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return out({'error':msg},status)
def user(req):
 h=req.headers.get('Authorization','')
 if h.startswith('Bearer '): return core.user_for_token(h[7:])
 t=req.query.get('token','')
 return core.user_for_token(t[7:] if t.startswith('Bearer ') else t) if t else None
async def body(req):
 try:return await req.json()
 except:return None
async def health(req): return out({'status':'ok','node_id':NODE})
async def register(req):
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
 with core.tx() as c:
  try:
   cur=c.execute("INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)",(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
   uid=cur.lastrowid; u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();t=core.issue_token(c,uid)
  except Exception:return err(409,'username already exists')
 return out({'user':core.user_to_json(u),'token':t},201)
async def login(req):
 b=await body(req)
 if not isinstance(b,dict):return err(400,'invalid body')
 c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username'),)).fetchone();c.close()
 if not u or not core.verify_password(b.get('password',''),u['password']):return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t})
async def me(req):
 u=user(req)
 return out({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspace_detail(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) is None:return err(403,'forbidden')
 c=core.connect(); inc=1 if req.query.get('include_archived','0') in ('1','true') else 0; rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?=1)',(w['id'],inc)).fetchall(); c.close(); rows=[x for x in rows if not x['is_private'] or core.can_read_channel(u['id'],x)]
 return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':[]})
async def workspace_members(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) is None:return err(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT user_id,role FROM workspace_members WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
 return out({'members':[{'user_id':x['user_id'],'role':x['role']} for x in rows]})
async def workspaces(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_ws(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('name'),str) or not b.get('name').strip() or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return err(400,'invalid workspace')
 with core.tx() as c:
  try:
   cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',b['slug']),u['id']));w=cur.lastrowid
   c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(w,u['id'],'owner'));ch=core.create_channel_row(c,w,'general',0,'',u['id']); wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone()
  except Exception:return err(409,'workspace exists')
 return out({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(ch)},201)
async def create_channel(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 slug=req.match_info['slug'];w=core.get_workspace(slug)
 if not w:return err(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if role is None or role=='guest':return err(403,'forbidden')
 b=await body(req)
 if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{1,32}',str(b.get('name',''))) or not isinstance(b.get('topic',''),str) or len(b.get('topic',''))>250:return err(400,'invalid channel')
 if bool(b.get('is_private',False)) and role not in ('owner','admin'):return err(403,'forbidden')
 with core.tx() as c:
  try:ch=core.create_channel_row(c,w['id'],b['name'],bool(b.get('is_private',False)),b.get('topic',''),u['id'])
  except Exception:return err(409,'channel exists')
 return out({'channel':core.channel_to_json(ch)},201)
async def post_message(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 if ch['is_archived']:return err(423,'archived')
 b=await body(req)
 if not isinstance(b,dict):return err(400,'invalid body')
 if not isinstance(b.get('body'),str):return err(400,'invalid body')
 try:
  result=core.create_message(u,ch['id'],b.get('body'),b.get('parent_id'))
  cc=core.connect(); members=cc.execute('SELECT u.id,u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall(); cc.close()
  names={x['username']:x['id'] for x in members}; result['mentions']=list(dict.fromkeys(names[n] for n in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',result['body']) if n in names and names[n]!=u['id']))
  return out({'message':result},201)
 except ValueError as e:return err(423,str(e)) if str(e)=='archived' else err(400,str(e))
 except ValueError as e:return err(423,str(e)) if str(e)=='archived' else err(400,str(e))
async def list_messages(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 if 'page' in req.query or 'offset' in req.query:return err(400,'offset pagination unsupported')
 try: limit,cursor=core.parse_paging(req.query,'messages')
 except: return err(400,'invalid paging')
 c=core.connect()
 if cursor is None: rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC LIMIT ?',(ch['id'],limit+1)).fetchall()
 else: rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 AND id<? ORDER BY id DESC LIMIT ?',(ch['id'],cursor,limit+1)).fetchall()
 c.close(); more=len(rows)>limit; rows=rows[:limit]
 return out({'messages':[core.message_to_json(x) for x in rows],'next_cursor':core.encode_cursor('messages',rows[-1]['id']) if more and rows else None})
async def channel_members(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 c=core.connect(); rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
 return out({'members':[core.user_to_json(x) for x in rs]})
async def patch_channel(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return err(400,'invalid topic')
 role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
 c0=core.connect(); cm=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
 if not cm and role!='owner':return err(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']))
  core.emit_event(c,ch['id'],'channel.updated',{})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def ws_handler(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ws=web.WebSocketResponse(); await ws.prepare(req); subscribed={}
 async def deliver():
  while not ws.closed:
   for cid,last in list(subscribed.items()):
    for frame in core.events_after(cid,last):
     await ws.send_json(frame); subscribed[cid]=frame['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(deliver())
 try:
  async for msg in ws:
   if msg.type!=web.WSMsgType.TEXT: continue
   try:d=json.loads(msg.data)
   except:continue
   cid=d.get('channel_id')
   if not isinstance(cid,int):continue
   if d.get('type')=='subscribe':
    subscribed[cid]=core.head_event_id(cid)
    await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subscribed[cid]})
   elif d.get('type')=='resume':
    since=d.get('since_event_id',0)
    subscribed[cid]=since
    for frame in core.events_after(cid,since):
     await ws.send_json(frame);subscribed[cid]=frame['event_id']
    await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':core.head_event_id(cid)})
 finally:
  task.cancel()
 return ws
async def root(req):
 return web.FileResponse('/app/static/index.html')
async def appjs(req):
 return web.FileResponse('/app/app.js',headers={'Content-Type':'application/javascript'})
async def join(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 try:return out({'channel':core.channel_to_json(core.join_channel(u,ch['id']))})
 except PermissionError:return err(403,'forbidden')
async def leave(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 with core.tx() as c: core.remove_channel_member(c,ch['id'],u['id'])
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def archive_channel(req, flag=None):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 if core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
 flag=req.path.endswith('/archive') if flag is None else flag
 if bool(ch['is_archived'])==flag:return out({'channel':core.channel_to_json(ch)})
 with core.tx() as c:
  c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(flag),ch['id']))
  core.emit_event(c,ch['id'],'channel.archived' if flag else 'channel.unarchived',{})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def patch_member(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 actor=core.member_role(w['id'],u['id'])
 if actor not in ('owner','admin'):return err(403,'forbidden')
 try: target=int(req.match_info['id'])
 except:return err(404,'not found')
 if not core.member_role(w['id'],target):return err(404,'not found')
 b=await body(req)
 if not isinstance(b,dict) or b.get('role') not in ('admin','member','guest'):return err(400,'invalid role')
 if target==w['owner_id'] or (actor=='admin' and core.member_role(w['id'],target)=='admin'):return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',(b['role'],w['id'],target))
 return out({'member':{'user_id':target,'role':b['role']}})
async def patch_workspace(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) is None:return err(403,'forbidden')
 b=await body(req)
 if not isinstance(b,dict) or ('name' in b and not isinstance(b['name'],str)) or ('join_mode' in b and not isinstance(b['join_mode'],str)):return err(400,'invalid body')
 return out({'workspace':core.workspace_to_json(w)})
async def transfer(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if u['id']!=w['owner_id']:return err(403,'forbidden')
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('new_owner_id'),int):return err(400,'invalid target')
 if core.member_role(w['id'],b['new_owner_id'])!='admin':return err(400,'target must be admin')
 with core.tx() as c:
  c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
  c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],b['new_owner_id']))
  c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(b['new_owner_id'],w['id']))
 return out({'workspace':core.workspace_to_json(core.get_workspace(req.match_info['slug']))})
async def update_workspace(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if role not in ('owner','admin'):return err(403,'forbidden')
 b=await body(req)
 if not isinstance(b,dict) or ('name' in b and not isinstance(b['name'],str)) or ('join_mode' in b and not isinstance(b['join_mode'],str)):return err(400,'invalid body')
 if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return err(400,'invalid join_mode')
 with core.tx() as c:c.execute('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?',(b.get('name'),b.get('join_mode'),w['id']))
 return out({'workspace':core.workspace_to_json(core.get_workspace(req.match_info['slug']))})
async def invitations(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT * FROM invitations WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 import datetime
 now=datetime.datetime.now(datetime.timezone.utc).timestamp()
 active=[dict(x) for x in rs if x['uses']<x['max_uses'] and (not x['expires_at'] or float(x['expires_at'])>now)]
 return out({'invitations':active})
async def replies(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
 if not r:return err(404,'not found')
 ch=core.get_channel(r['channel_id'])
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(r['id'],)).fetchall();c.close()
 return out({'replies':[core.message_to_json(x) for x in rs],'next_cursor':None})
async def pin_message(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch=core.get_channel(m['channel_id'])
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(m['id'],u['id'],core.now_iso()))
  r=c.execute('SELECT * FROM pins WHERE message_id=?',(m['id'],)).fetchone()
 return out({'pin':{'message_id':r['message_id'],'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']}})
async def unpin_message(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
 if not m:return err(404,'not found')
 if not core.can_read_channel(u['id'],core.get_channel(m['channel_id'])):return err(403,'forbidden')
 with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(m['id'],))
 return out({'unpinned':True})
async def list_pins(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 try:limit,cursor=core.parse_paging(req.query,'pins')
 except:return err(400,'invalid paging')
 c=core.connect()
 if cursor is None: rs=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC LIMIT ?',(ch['id'],limit+1)).fetchall()
 else: rs=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND p.message_id<? ORDER BY p.pinned_at DESC LIMIT ?',(ch['id'],cursor,limit+1)).fetchall()
 c.close();more=len(rs)>limit;rs=rs[:limit]
 return out({'pins':[{'message':core.message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':core.encode_cursor('pins',rs[-1]['message_id']) if more and rs else None})
async def search(req):
 u=user(req); 
 if not u:return err(401,'unauthorized')
 q=req.query.get('q')
 if not isinstance(q,str) or not q:return err(400,'query required')
 try:limit,cursor=core.parse_paging(req.query,'search')
 except:return err(400,'invalid paging')
 c=core.connect(); pattern='%'+q.lower()+'%'
 rows=c.execute('SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE lower(m.body) LIKE ? AND m.deleted=0 ORDER BY m.id DESC LIMIT ?', (pattern,limit+1)).fetchall();c.close()
 more=len(rows)>limit;rows=rows[:limit]
 return out({'results':[core.message_to_json(x) for x in rows],'next_cursor':core.encode_cursor('search',rows[-1]['id']) if more and rows else None})
async def create_invite(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 b=await body(req)
 if not isinstance(b,dict):return err(400,'invalid body')
 if 'max_uses' in b and (not isinstance(b['max_uses'],int) or isinstance(b['max_uses'],bool) or b['max_uses']<1):return err(400,'invalid max_uses')
 if 'expires_in_seconds' in b and (not isinstance(b['expires_in_seconds'],int) or isinstance(b['expires_in_seconds'],bool) or b['expires_in_seconds']<1):return err(400,'invalid expiry')
 target=b.get('invited_user_id')
 if target is None and b.get('invited_username') is not None:
  if not isinstance(b['invited_username'],str):return err(400,'invalid invite target')
  cc=core.connect(); ur=cc.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone(); cc.close()
  if ur: target=ur['id']
 code=__import__('secrets').token_urlsafe(12)
 expires=None
 if b.get('expires_in_seconds'): expires=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).timestamp()+b['expires_in_seconds']
 with core.tx() as c:
  c.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,max_uses,expires_at,created_at) VALUES(?,?,?,?,?,?)',(code,w['id'],target,1 if target is not None else b.get('max_uses',1),str(expires) if expires else None,core.now_iso()))
  r=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
 return out({'invitation':dict(r)},201)
async def channel_read(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 c=core.connect();r=c.execute('SELECT * FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone();head=core.head_event_id(ch['id']);c.close()
 last=r['last_read_event_id'] if r else 0
 return out({'read_state':{'channel_id':ch['id'],'last_read_event_id':last,'unread_count':max(0,head-last),'mention_count':r['mention_count'] if r else 0}})
async def mark_channel_read(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('last_read_event_id'),int) or isinstance(b.get('last_read_event_id'),bool):return err(400,'invalid event id')
 with core.tx() as c:
  old=c.execute('SELECT last_read_event_id,mention_count FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()
  n=max(b['last_read_event_id'],old['last_read_event_id'] if old else 0)
  c.execute('INSERT INTO read_states(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=?',(u['id'],ch['id'],n,n))
 return await channel_read(req)
async def accept_invite(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 c=core.connect();r=c.execute('SELECT * FROM invitations WHERE code=?',(req.match_info['code'],)).fetchone();c.close()
 if not r:return err(404,'not found')
 import datetime
 if r['uses']>=r['max_uses'] or (r['expires_at'] and float(r['expires_at'])<=datetime.datetime.now(datetime.timezone.utc).timestamp()):return err(404,'not found')
 if r['invited_user_id'] is not None and r['invited_user_id']!=u['id']:return err(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(r['code'],));core.add_workspace_member(c,r['workspace_id'],u['id'],'member')
 w=core.get_workspace_by_id(r['workspace_id'])
 return out({'workspace':core.workspace_to_json(w)})
async def revoke_invite(req):
 u=user(req)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 with core.tx() as c:n=c.execute('DELETE FROM invitations WHERE code=? AND workspace_id=?',(req.match_info['code'],w['id'])).rowcount
 return out({'deleted':True}) if n else err(404,'not found')
app=web.Application()
app.router.add_get('/api/health',health);app.router.add_get('/api/ws',ws_handler);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_patch('/api/workspaces/{slug}',update_workspace);app.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer);app.router.add_get('/api/workspaces/{slug}/invitations',invitations);app.router.add_post('/api/invitations/{code}/accept',accept_invite);app.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_invite);app.router.add_post('/api/workspaces/{slug}/invitations',create_invite);app.router.add_patch('/api/workspaces/{slug}',patch_workspace);app.router.add_get('/api/workspaces/{slug}',workspace_detail);app.router.add_get('/api/workspaces/{slug}/members',workspace_members);app.router.add_post('/api/workspaces',create_ws);app.router.add_post('/api/workspaces/{slug}/channels',create_channel);app.router.add_patch('/api/workspaces/{slug}/members/{id}',patch_member)
app.router.add_patch('/api/channels/{id}',patch_channel);app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_get('/api/channels/{id}/pins',list_pins);app.router.add_get('/api/channels/{id}/read',channel_read);app.router.add_post('/api/channels/{id}/read',mark_channel_read);app.router.add_get('/api/search',search);app.router.add_post('/api/messages/{id}/pin',pin_message);app.router.add_delete('/api/messages/{id}/pin',unpin_message);app.router.add_post('/api/channels/{id}/archive',lambda r: archive_channel(r,True));app.router.add_post('/api/channels/{id}/unarchive',lambda r: archive_channel(r,False));app.router.add_get('/api/channels/{id}/members',channel_members);app.router.add_post('/api/channels/{id}/join',join);app.router.add_post('/api/channels/{id}/leave',leave);app.router.add_post('/api/channels/{id}/messages',post_message);app.router.add_get('/api/channels/{id}/messages',list_messages);app.router.add_get('/',root);app.router.add_get('/app.js',appjs)
web.run_app(app,host='127.0.0.1',port=8000+NODE)
