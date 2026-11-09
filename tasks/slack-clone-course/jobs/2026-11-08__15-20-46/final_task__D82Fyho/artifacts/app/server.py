import asyncio, argparse, json, re, os
from aiohttp import web, WSMsgType
import core
NODE=0
async def read_json(r):
 try:
  x=await r.json(); return x if isinstance(x,dict) else None
 except: return None
def out(x,status=200): return web.json_response(x,status=status)
def err(status,msg): return out({'error':msg},status)
def auth(r):
 h=r.headers.get('Authorization','')
 return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
async def require(r):
 u=auth(r)
 return u if u else None
def accessible(u,ch):
 if not ch:return False
 if ch['is_dm']: return bool(core.connect().execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone())
 return bool(core.connect().execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone())
def msgjson(r):
 d=core.message_to_json(r); c=core.connect()
 d['reactions']=core.reactions_for(r['id'],c) if hasattr(core,'reactions_for') else []
 d['files']=[core.file_to_json(x) for x in c.execute('SELECT * FROM files WHERE message_id=? ORDER BY id',(r['id'],)).fetchall()]
 d['mentions']=core.resolve_mentions(c,r['body'],core.get_channel(r['channel_id']),r['author_id'])
 d['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
 c.close(); return d
async def replies(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();parent=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not parent:return err(404,'not found')
 ch=core.get_channel(parent['channel_id'])
 if not accessible(u,ch):return err(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(parent['id'],)).fetchall();c.close()
 return out({'replies':[msgjson(x) for x in rows],'next_cursor':None})

async def editmsg(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not m:return err(404,'not found')
 b=await read_json(r)
 if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'invalid body')
 try:x=core.edit_message(u,int(r.match_info['id']),b['body'])
 except RuntimeError:return err(409,'deleted')
 except PermissionError:return err(403,'forbidden')
 return out({'message':msgjson(x)})
async def deletemsg(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not m:return err(404,'not found')
 try:core.delete_message(u,int(r.match_info['id']))
 except PermissionError:return err(403,'forbidden')
 return out({'deleted':True})

async def index(r): return web.FileResponse('/app/static/index.html',headers={'Content-Type':'text/html'})
async def health(r): return out({'status':'ok','node_id':NODE})
async def register(r):
 b=await read_json(r)
 if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
 with core.tx() as c:
  try:
   u=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?) RETURNING *',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'])).fetchone()
   t=core.issue_token(c,u['id'])
  except: return err(409,'username already exists')
 return out({'user':core.user_to_json(u),'token':t},201)
async def login(r):
 b=await read_json(r); c=core.connect()
 u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if b else '',)).fetchone(); c.close()
 if not u or not isinstance(b.get('password') if b else None,str) or not core.verify_password(b['password'],u['password']):return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=auth(r); return out({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def create_ws(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 b=await read_json(r)
 if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')) or not isinstance(b.get('name'),str):return err(400,'invalid workspace')
 with core.tx() as c:
  try:w=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?) RETURNING *',(b['slug'],b['name'],u['id'])).fetchone()
  except:return err(409,'duplicate workspace')
  ch=core.create_channel_row(c,w['id'],'general'); core.add_workspace_member(c,w['id'],u['id'],'owner'); core.add_channel_member(c,ch['id'],u['id'])
 return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
async def workspaces(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def ws_detail(r):
 u=auth(r); w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
 return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in ch],'read_state':{}})
async def create_channel(r):
 u=auth(r); w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 b=await read_json(r)
 if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return err(400,'invalid channel')
 with core.tx() as c:
  if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return err(409,'duplicate channel')
  ch=core.create_channel_row(c,w['id'],b['name'],bool(b.get('is_private')),b.get('topic',''));core.add_channel_member(c,ch['id'],u['id'])
 return out({'channel':core.channel_to_json(ch)},201)
async def patch_channel(r):
 u=auth(r); ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not accessible(u,ch) and (not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner'):return err(403,'forbidden')
 b=await read_json(r)
 if not b or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return err(400,'invalid topic')
 with core.tx() as c:
  c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']))
  ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
  core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(ch)})
 return out({'channel':core.channel_to_json(ch)})

async def join(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 with core.tx() as c:
  core.add_channel_member(c,ch['id'],u['id'])
  if ch['workspace_id']:core.add_workspace_member(c,ch['workspace_id'],u['id'])
 return out({'channel':core.channel_to_json(ch)})
async def postmsg(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not accessible(u,ch):return err(403,'forbidden')
 b=await read_json(r)
 if not b or not isinstance(b.get('body'),str):return err(400,'invalid body')
 command=core.run_slash(u,ch,b['body'])
 if command.get('forbidden'):return err(403,'forbidden')
 if 'error' in command:return err(400,command['error'])
 if 'side' in command:return out({'message':None,command['side']:core.channel_to_json(command['channel']) if command['side']=='channel' else command['invited_user']},201)
 if ch['is_archived']:return err(423,'archived')
 b['body']=command['body']
 if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty body')
 x=core.create_message(u,ch['id'],b['body'],b.get('parent_id'))
 if isinstance(b.get('file_ids'),list):
  with core.tx() as c:
   for fid in b['file_ids']:
    f=c.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone()
    if not f:return err(400,'invalid file')
    c.execute('UPDATE files SET message_id=? WHERE id=?',(x['id'],fid))
 return out({'message':msgjson(x)},201)
async def messages(r):
 u=auth(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not accessible(u,ch):return err(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(ch['id'],)).fetchall();c.close()
 return out({'messages':[msgjson(x) for x in rows],'next_cursor':None})
async def react(r):
 u=auth(r);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not u:return err(401,'unauthorized')
 if not m:return err(404,'not found')
 ch=core.get_channel(m['channel_id']); b=await read_json(r)
 emoji=b.get('emoji') if b else None
 if not isinstance(emoji,str) or not emoji:return err(400,'invalid emoji')
 if not accessible(u,ch):return err(403,'forbidden')
 if ch['is_archived']:return err(423,'archived')
 with core.tx() as c:
  exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],emoji)).fetchone()
  if not exists:
   c.execute('INSERT INTO reactions VALUES(?,?,?)',(m['id'],u['id'],emoji));core.emit_event(c,ch['id'],'reaction.added',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
 c=core.connect(); rows=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY min(rowid)',(m['id'],)).fetchall();c.close()
 return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rows]})
async def unreact(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch=core.get_channel(m['channel_id'])
 if not accessible(u,ch):return err(403,'forbidden')
 if ch['is_archived']:return err(423,'archived')
 emoji=r.match_info.get('emoji','')
 with core.tx() as c:
  hit=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],emoji)).fetchone()
  if hit:
   c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],emoji))
   core.emit_event(c,ch['id'],'reaction.removed',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
 c=core.connect(); rows=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY min(rowid)',(m['id'],)).fetchall();c.close()
 return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rows]})

async def groups(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT * FROM groups WHERE workspace_id=? ORDER BY id',(w['id'],)).fetchall();c.close()
 return out({'groups':[core.group_to_json(x) for x in rows]})
async def group_create(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 b=await read_json(r)
 if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(b.get('user_ids',[]),list) or not all(isinstance(x,int) for x in b.get('user_ids',[])):return err(400,'invalid group')
 try:g=core.create_group(w['id'],u['id'],b['handle'],b['name'],b.get('user_ids',[]))
 except ValueError as e:return err(409 if str(e)=='duplicate' else 400,'invalid group')
 return out({'group':core.group_to_json(g)},201)
async def group_change(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 c=core.connect();g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone();c.close()
 if not g:return err(404,'not found')
 if g['creator_id']!=u['id'] and core.member_role(w['id'],u['id'])!='owner':return err(403,'forbidden')
 b=await read_json(r) or {}
 if 'user_ids' in b and (not isinstance(b['user_ids'],list) or not all(isinstance(x,int) for x in b['user_ids'])):return err(400,'invalid members')
 if 'user_ids' in b:
  c=core.connect();ok=all(c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone() for x in b['user_ids']);c.close()
  if not ok:return err(400,'invalid members')
 g=core.update_group(g['id'],b.get('name'),b.get('user_ids'));return out({'group':core.group_to_json(g)})
async def group_delete(r):
 u=auth(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 c=core.connect();g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone();c.close()
 if g and g['creator_id']!=u['id'] and core.member_role(w['id'],u['id'])!='owner':return err(403,'forbidden')
 if g:core.delete_group(g['id'])
 return out({'deleted':True})

async def profile(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); x=c.execute('SELECT * FROM users WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 return out({'user':core.user_to_json(x)}) if x else err(404,'not found')
async def profile_update(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 b=await read_json(r)
 if b is None:return err(400,'invalid body')
 try:x=core.update_profile(u,b)
 except ValueError as e:return err(400,str(e))
 return out({'user':core.user_to_json(x)})
async def auth_placeholder(r):
 if not auth(r): return err(401,'unauthorized')
 return err(404,'not found')
async def channel_members(r):
 u=auth(r); ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not accessible(u,ch):return err(403,'forbidden')
 c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
 return out({'members':[core.user_to_json(x) for x in rows]})

async def dm(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 b=await read_json(r); oid=b.get('user_id') if b else None
 if not isinstance(oid,int):return err(400,'invalid user_id')
 if not core.user_for_id(oid):return err(404,'not found')
 return out({'channel_id':core.open_dm(u,oid)})
async def upload(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 try:
  q=await r.multipart(); part=await q.next()
  if not part or part.name!='file':return err(400,'file required')
  data=await part.read()
 except:return err(400,'invalid upload')
 if len(data)>10485760:return err(413,'file too large')
 f=core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)
 return out({'file':core.file_to_json(f)},201)
async def filemeta(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();f=c.execute('SELECT * FROM files WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 return out({'file':core.file_to_json(f)}) if f else err(404,'not found')
async def download(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();f=c.execute('SELECT * FROM files WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not f:return err(404,'not found')
 return web.Response(body=open('/app/data/files/'+str(f['id']),'rb').read(),content_type=f['content_type'],headers={'Content-Disposition':'attachment; filename="'+f['filename']+'"'})
async def ws(r):
 u=auth(r) or core.user_for_token(r.query.get('token'))
 if not u:return err(401,'unauthorized')
 conn_id=__import__('secrets').token_urlsafe(12); core.touch_presence(conn_id,u['id'])
 w=web.WebSocketResponse();await w.prepare(r); subs={}; global_seen=0
 async def pump():
  while not w.closed:
   nonlocal global_seen
   for gid,e in core.globals_after(global_seen):
    await w.send_json(e); global_seen=gid
   core.touch_presence(conn_id,u['id'])
   for ch,last in list(subs.items()):
    for e in core.events_after(ch,last):await w.send_json(e);subs[ch]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(pump())
 try:
  async for m in w:
   if m.type==WSMsgType.TEXT:
    x=json.loads(m.data)
    if x.get('type')=='subscribe':
     ch=int(x['channel_id']); head=core.head_event_id(ch); subs[ch]=head; await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
    elif x.get('type')=='resume':
     ch=int(x['channel_id']); subs[ch]=int(x.get('since_event_id',0)); await asyncio.sleep(0.06); await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
 finally:
  task.cancel(); core.drop_presence(conn_id)
 return w
def app():
 a=web.Application(client_max_size=11*1024*1024);a.router.add_get('/',index);a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces',workspaces);a.router.add_get('/api/workspaces/{slug}',ws_detail);a.router.add_post('/api/workspaces/{slug}/channels',create_channel);a.router.add_post('/api/channels/{id}/join',join);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_post('/api/channels/{id}/messages',postmsg);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_post('/api/messages/{id}/reactions',react);a.router.add_patch('/api/messages/{id}',editmsg);a.router.add_delete('/api/messages/{id}',deletemsg);a.router.add_post('/api/dms',dm);a.router.add_post('/api/files',upload);a.router.add_get('/api/workspaces/{slug}/groups',groups);a.router.add_get('/api/users/{id}',profile);a.router.add_patch('/api/users/me',profile_update);a.router.add_post('/api/workspaces/{slug}/groups',group_create);a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',group_change);a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',group_delete);a.router.add_post('/api/files',auth_placeholder);a.router.add_get('/api/files/{id}',filemeta);a.router.add_get('/api/files/{id}/download',download);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',unreact);a.router.add_get('/api/ws',ws);return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(app(),host='127.0.0.1',port=8000+NODE)
