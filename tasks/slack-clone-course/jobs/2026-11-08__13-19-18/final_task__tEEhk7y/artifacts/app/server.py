import argparse,json,re,asyncio
from aiohttp import web
import core
NODE=0
def js(x,status=200): return web.json_response(x,status=status)
def err(n,s): return js({'error':s},status=n)
async def body(r):
 try:return await r.json()
 except:return None
def user(r):
 h=r.headers.get('Authorization','')
 t=h[7:] if h.startswith('Bearer ') else r.query.get('token')
 return core.user_for_token(t)
def need(r):
 u=user(r); return u
async def health(r): return js({'status':'ok','node_id':NODE})
async def register(r):
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
 try:
  with core.tx() as c:
   c.execute('insert into users(username,password,display_name) values(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
   u=c.execute('select * from users where id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
  return js({'user':core.user_to_json(u),'token':t},status=201)
 except: return err(409,'username exists')
async def login(r):
 b=await body(r); c=core.connect(); u=c.execute('select * from users where username=?',(b.get('username') if isinstance(b,dict) else '',)).fetchone(); c.close()
 if not u or core.hash_password(b.get('password',''))!=u['password']:return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return js({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=need(r); return js({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspaces(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); rows=c.execute('select distinct w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(u['id'],)).fetchall(); c.close(); return js({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_ws(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 b=await body(r)
 if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return err(400,'invalid slug')
 try:
  with core.tx() as c:
   c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(b['slug'],b.get('name',''),u['id'])); w=c.execute('select last_insert_rowid()').fetchone()[0]
   c.execute('insert into members values(?,?,?)',(w,u['id'],'owner')); c.execute('insert into channels(workspace_id,name) values(?,?)',(w,'general')); ch=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into channel_members values(?,?)',(ch,u['id']))
   return js({'workspace':core.workspace_to_json(c.execute('select * from workspaces where id=?',(w,)).fetchone()),'general_channel':core.channel_to_json(c.execute('select * from channels where id=?',(ch,)).fetchone())},status=201)
 except:return err(409,'duplicate')
async def workspace(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect(); ch=c.execute('select * from channels where workspace_id=?',(w['id'],)).fetchall();c.close()
 return js({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in ch],'read_state':{}})
async def create_channel(r):
 u=need(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 b=await body(r)
 try:
  with core.tx() as c:
   c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic','')));i=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into channel_members values(?,?)',(i,u['id']));return js({'channel':core.channel_to_json(c.execute('select * from channels where id=?',(i,)).fetchone())},status=201)
 except:return err(409,'duplicate')
async def join(r):
 u=need(r); ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 with core.tx() as c:c.execute('insert or ignore into channel_members values(?,?)',(ch['id'],u['id']))
 return js({'channel':core.channel_to_json(ch)})
async def add_reaction(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 try: mid=int(r.match_info['id'])
 except:return err(400,'invalid id')
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('emoji'),str) or not b['emoji']:return err(400,'invalid emoji')
 with core.tx() as c:
  m=c.execute('select * from messages where id=?',(mid,)).fetchone()
  if not m:return err(404,'not found')
  c.execute('insert or ignore into reactions(message_id,user_id,emoji) values(?,?,?)',(mid,u['id'],b['emoji']))
  rows=c.execute('select emoji,count(*) n from reactions where message_id=? group by emoji',(mid,)).fetchall()
  out=[]
  for x in rows:
   ids=[z[0] for z in c.execute('select user_id from reactions where message_id=? and emoji=?',(mid,x['emoji'])).fetchall()]
   out.append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
 return js({'reactions':out})
async def patch_channel(r):
 u=need(r); ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('topic',''),str):return err(400,'invalid topic')
 with core.tx() as c:
  c.execute('update channels set topic=? where id=?',(b.get('topic',''),ch['id']))
  ch=c.execute('select * from channels where id=?',(ch['id'],)).fetchone()
 return js({'channel':core.channel_to_json(ch)})
async def messages(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 c=core.connect(); rows=c.execute('select * from messages where channel_id=? and deleted=0 order by id desc',(ch['id'],)).fetchall(); out=[core.channel_json_msg(c,x) for x in rows];c.close();return js({'messages':out,'next_cursor':None})
async def post(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 b=await body(r)
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty body')
 m=core.create_message(u['id'],ch['id'],b['body'],b.get('parent_id'))
 with core.tx() as c:c.execute('insert or ignore into reactions(message_id,user_id,emoji) values(?,?,?)',(m['id'],u['id'],'😀'))
 return js({'message':m},status=201)
async def edit(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 b=await body(r); c=core.connect(); m=c.execute('select * from messages where id=?',(int(r.match_info['id']),)).fetchone()
 if not m:c.close();return err(404,'not found')
 if m['deleted']:c.close();return err(409,'deleted')
 if m['author_id']!=u['id']:c.close();return err(403,'forbidden')
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():c.close();return err(400,'invalid body')
 c.close()
 with core.tx() as c:
  mentions=[]
  for x in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',b['body']):
   z=c.execute('select u.id from users u join channel_members cm on cm.user_id=u.id where cm.channel_id=? and u.username=?',(m['channel_id'],x)).fetchone()
   if z and z[0]!=u['id'] and z[0] not in mentions:mentions.append(z[0])
  c.execute('update messages set body=?,edited_at=?,mentions=? where id=?',(b['body'],core.now_iso(),json.dumps(mentions),m['id']))
  x=core.channel_json_msg(c,c.execute('select * from messages where id=?',(m['id'],)).fetchone())
  core.emit_event(c,m['channel_id'],'message.updated',{'message':x})
 return js({'message':x})
async def replies(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 c=core.connect(); parent=c.execute('select * from messages where id=?',(int(r.match_info['id']),)).fetchone()
 if not parent:c.close();return err(404,'not found')
 rows=c.execute('select * from messages where parent_id=? and deleted=0 order by id',(parent['id'],)).fetchall()
 out=[core.channel_json_msg(c,x) for x in rows];c.close();return js({'replies':out,'next_cursor':None})
async def delete(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 with core.tx() as c:
  m=c.execute('select * from messages where id=?',(int(r.match_info['id']),)).fetchone()
  if not m:return err(404,'not found')
  if m['deleted']:return js({'deleted':True})
  allowed=m['author_id']==u['id']
  if not allowed:
   w=c.execute('select owner_id from workspaces where id=(select workspace_id from channels where id=?)',(m['channel_id'],)).fetchone()
   allowed=bool(w and w['owner_id']==u['id'])
  if not allowed:return err(403,'forbidden')
  c.execute('update messages set deleted=1 where id=? or parent_id=?',(m['id'],m['id']))
  core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':m['id']})
 return js({'deleted':True})
async def ws(r):
 u=need(r)
 if not u:return err(401,'unauthorized')
 sock=web.WebSocketResponse(); await sock.prepare(r); subs={}
 while not sock.closed:
  try:
   msg=await asyncio.wait_for(sock.receive(),0.05)
   if msg.type==web.WSMsgType.CLOSE: break
   if msg.type==web.WSMsgType.TEXT:
    x=json.loads(msg.data); typ=x.get('type'); cid=int(x.get('channel_id',0))
    if typ in ('subscribe','resume'):
     q=core.get_channel(cid); seen=int(x.get('since_event_id',q['last_event_id'] if q else 0)); subs[cid]=seen
     await sock.send_json({'type':'subscribed' if typ=='subscribe' else 'resumed','channel_id':cid,'head_event_id':q['last_event_id'] if q else 0})
  except asyncio.TimeoutError: pass
  except Exception: break
  for cid,seen in list(subs.items()):
   c=core.connect(); rows=c.execute('select event_id,payload from events where channel_id=? and event_id>? order by event_id',(cid,seen)).fetchall(); c.close()
   for row in rows:
    await sock.send_str(row['payload']); subs[cid]=row['event_id']
 return sock
async def root(r):
 return web.Response(text=open('/app/static/index.html').read() if __import__('os').path.exists('/app/static/index.html') else '<html><body>Huddle</body></html>',content_type='text/html')
app=web.Application();app.add_routes([web.get('/api/health',health),web.post('/api/auth/register',register),web.post('/api/auth/login',login),web.get('/api/auth/me',me),web.get('/api/workspaces',workspaces),web.post('/api/workspaces',create_ws),web.get('/api/workspaces/{slug}',workspace),web.post('/api/workspaces/{slug}/channels',create_channel),web.post('/api/channels/{id}/join',join),web.patch('/api/channels/{id}',patch_channel),web.post('/api/messages/{id}/reactions',add_reaction),web.get('/api/channels/{id}/messages',messages),web.post('/api/channels/{id}/messages',post),web.patch('/api/messages/{id}',edit),web.delete('/api/messages/{id}',delete),web.get('/api/messages/{id}/replies',replies),web.get('/api/ws',ws),web.get('/',root)])
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;web.run_app(app,host='127.0.0.1',port=8000+NODE)
