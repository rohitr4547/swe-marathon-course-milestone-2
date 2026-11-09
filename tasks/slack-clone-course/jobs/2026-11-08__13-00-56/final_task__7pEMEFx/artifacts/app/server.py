import asyncio, argparse, json, re
from aiohttp import web, WSMsgType
import core
NODE=0
def json_error(s,m): return web.json_response({'error':m},status=s)
async def read_json(req):
 try:
  x=await req.json(); return x if isinstance(x,dict) else None
 except: return None
def require_user(req):
 h=req.headers.get('Authorization','')
 return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
def ok(x,status=200): return web.json_response(x,status=status)
def auth(req):
 u=require_user(req); return u
def channel_json(ch): return core.channel_to_json(ch)
async def health(req): return ok({'status':'ok','node_id':NODE})
async def register(req):
 d=await read_json(req)
 if not d or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d['username']) or not isinstance(d.get('password'),str) or len(d['password'])<8:return json_error(400,'invalid registration')
 try:
  with core.tx() as c:
   cur=c.execute("INSERT INTO users(username,password,display_name) VALUES(?,?,?)",(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
   uid=cur.lastrowid; t=core.issue_token(c,uid); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
  return ok({'user':core.user_to_json(u),'token':t},201)
 except Exception:return json_error(409,'username already exists')
async def login(req):
 d=await read_json(req); c=core.connect()
 u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if d else '',)).fetchone(); c.close()
 if not u or not isinstance(d.get('password') if d else None,str) or not core.verify_password(d['password'],u['password']):return json_error(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return ok({'user':core.user_to_json(u),'token':t})
async def me(req):
 u=auth(req)
 return ok({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def workspaces(req):
 u=auth(req)
 if not u:return json_error(401,'unauthorized')
 c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN wmembers m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return ok({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(req):
 u=auth(req)
 if not u:return json_error(401,'unauthorized')
 d=await read_json(req)
 if not d or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d['slug']) or not isinstance(d.get('name'),str):return json_error(400,'invalid workspace')
 try:
  with core.tx() as c:
   w=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id'])).lastrowid
   ch=core.create_channel_row(c,w,'general');core.add_workspace_member(c,w,u['id'],'owner'); chrow=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone(); wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone()
  return ok({'workspace':core.workspace_to_json(wr),'general_channel':channel_json(chrow)},201)
 except: return json_error(409,'duplicate workspace')
async def workspace(req):
 u=auth(req)
 if not u:return json_error(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return json_error(404,'not found')
 c=core.connect(); cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
 return ok({'workspace':core.workspace_to_json(w),'channels':[channel_json(x) for x in cs],'read_state':{}})
async def create_channel(req):
 u=auth(req)
 if not u:return json_error(401,'unauthorized')
 w=core.get_workspace(req.match_info['slug'])
 if not w:return json_error(404,'not found')
 if core.member_role(w['id'],u['id']) is None:return json_error(403,'forbidden')
 d=await read_json(req)
 if not d or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']):return json_error(400,'invalid channel')
 try:
  with core.tx() as c:
   if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone(): return json_error(409,'duplicate channel')
   cid=core.create_channel_row(c,w['id'],d['name'],bool(d.get('is_private')),d.get('topic',''));core.add_channel_member(c,cid,u['id']); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  return ok({'channel':channel_json(r)},201)
 except:return json_error(409,'duplicate channel')
async def reaction(req):
 u=auth(req)
 if not u:return json_error(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
 if not m:return json_error(404,'not found')
 d=await read_json(req)
 if not d or not isinstance(d.get('emoji'),str) or not d['emoji']:return json_error(400,'invalid emoji')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)',(m['id'],u['id'],d['emoji']))
  rows=c.execute('SELECT emoji,COUNT(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(m['id'],)).fetchall()
 return ok({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[]} for x in rows]})

async def channel_detail(req):
 u=auth(req); ch=core.get_channel(req.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
 return ok({'channel':core.channel_to_json(ch)})
async def patch_channel(req):
 u=auth(req); ch=core.get_channel(req.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 d=await read_json(req)
 if not d or not isinstance(d.get('topic'),str) or len(d['topic'])>250:return json_error(400,'invalid topic')
 if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
 with core.tx() as c:
  c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],ch['id']))
  r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
  core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(r)})
 return ok({'channel':core.channel_to_json(r)})
async def join(req):
 u=auth(req); ch=core.get_channel(req.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 with core.tx() as c:
  core.add_channel_member(c,ch['id'],u['id'])
 return ok({'channel':channel_json(ch)})
async def messages(req):
 u=auth(req);ch=core.get_channel(req.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(ch['id'],)).fetchall();c.close()
 return ok({'messages':[core.message_to_json(x) for x in rows],'next_cursor':None})
async def post(req):
 u=auth(req);ch=core.get_channel(req.match_info['id'])
 if not u:return json_error(401,'unauthorized')
 if not ch:return json_error(404,'not found')
 if ch['is_archived']:return json_error(423,'archived')
 d=await read_json(req)
 if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'empty body')
 return ok({'message':core.create_message(u,ch['id'],d['body'],d.get('parent_id'))},201)
async def replies(req):
 u=auth(req); mid=req.match_info['id']
 if not u:return json_error(401,'unauthorized')
 c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,)).fetchall();c.close()
 return ok({'replies':[core.message_to_json(x) for x in rows],'next_cursor':None})
async def edit(req):
 u=auth(req)
 if not u:return json_error(401,'unauthorized')
 d=await read_json(req)
 if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'invalid body')
 c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
 if not r:return json_error(404,'not found')
 if r['deleted']:return json_error(409,'deleted')
 if r['author_id']!=u['id']:return json_error(403,'forbidden')
 with core.tx() as c:
  t=core.now_iso();c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],t,r['id'])); nr=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone();core.emit_event(c,r['channel_id'],'message.updated',{'message':core.message_to_json(nr)})
 return ok({'message':core.message_to_json(nr)})
async def delete(req):
 u=auth(req)
 if not u:return json_error(401,'unauthorized')
 c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
 if not r:return json_error(404,'not found')
 owner=False
 ch=core.get_channel(r['channel_id'])
 if ch and ch['workspace_id'] is not None:
  w=core.get_workspace(next((x['slug'] for x in []),'')) if False else None
  cc=core.connect(); ow=cc.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); cc.close(); owner=bool(ow and ow['owner_id']==u['id'])
 if r['author_id']!=u['id'] and not owner:return json_error(403,'forbidden')
 if not r['deleted']:
  with core.tx() as c:c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],));core.emit_event(c,r['channel_id'],'message.deleted',{'message_id':r['id']})
 return ok({'deleted':True})
async def ws(req):
 u=core.user_for_token(req.query.get('token')); 
 if not u:return web.Response(status=401)
 w=web.WebSocketResponse();await w.prepare(req);last={}
 async def pump():
  while not w.closed:
   for ch,n in list(last.items()):
    for e in core.events_after(ch,n): await w.send_json(e);last[ch]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(pump())
 try:
  async for m in w:
   if m.type==WSMsgType.TEXT:
    d=json.loads(m.data);ch=int(d.get('channel_id',0))
    if d.get('type')=='subscribe':last[ch]=core.head_event_id(ch);await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':last[ch]})
    elif d.get('type')=='resume':last[ch]=int(d.get('since_event_id',0));await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
 finally:task.cancel()
 return w
async def index(req):
 return web.FileResponse('/app/static/index.html')
app=web.Application()
for p,h in [('/api/health',health),('/api/auth/register',register),('/api/auth/login',login),('/api/auth/me',me),('/api/workspaces',workspaces),('/api/workspaces',create_workspace)]:
 app.router.add_route('POST' if h in (register,login,create_workspace) else 'GET',p,h)
app.router.add_get('/',index);app.router.add_get('/api/workspaces/{slug}',workspace);app.router.add_post('/api/workspaces/{slug}/channels',create_channel)
app.router.add_post('/api/channels/{id}/join',join);app.router.add_get('/api/channels/{id}',channel_detail);app.router.add_patch('/api/channels/{id}',patch_channel);app.router.add_post('/api/messages/{id}/reactions',reaction);app.router.add_get('/api/channels/{id}/messages',messages);app.router.add_post('/api/channels/{id}/messages',post);app.router.add_get('/api/messages/{id}/replies',replies)
app.router.add_patch('/api/messages/{id}',edit);app.router.add_delete('/api/messages/{id}',delete);app.router.add_get('/api/ws',ws)
if __name__=='__main__':
 core.init_db();p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;web.run_app(app,host='127.0.0.1',port=8000+NODE)
