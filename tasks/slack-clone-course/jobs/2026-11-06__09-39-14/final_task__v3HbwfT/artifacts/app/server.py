import asyncio,json,os,re,argparse
from aiohttp import web,WSMsgType
import core
core.init_db(); sockets=set()
def tok(req): return (req.headers.get('Authorization') or '').replace('Bearer ','')
def user(req): return core.user_for_token(tok(req))
def out(d,status=200): return web.json_response(d,status=status)
def err(s,m): return out({'error':m},s)
async def body(req):
 try:return await req.json()
 except:return None
def auth(req):
 u=user(req);return u
async def api(req):
 p=req.path
 if p=='/api/health':return out({'status':'ok','node_id':req.app['node']})
 if p=='/api/auth/register':
  b=await body(req)
  if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
  try:
   with core.tx() as c:
    x=c.execute('insert into users(username,password,display_name) values(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']));u=c.execute('select * from users where id=?',(x.lastrowid,)).fetchone();t=core.issue_token(c,u['id'])
   return out({'user':core.user_to_json(u),'token':t},201)
  except:return err(409,'username already exists')
 if p=='/api/auth/login':
  b=await body(req);c=core.connect();u=c.execute('select * from users where username=?',(b.get('username') if isinstance(b,dict) else '',)).fetchone();c.close()
  if not u or not core.verify_password(b.get('password',''),u['password']):return err(401,'invalid credentials')
  with core.tx() as c:t=core.issue_token(c,u['id'])
  return out({'user':core.user_to_json(u),'token':t})
 if p=='/api/auth/me':
  u=auth(req);return out({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
 u=auth(req)
 if not u:return err(401,'unauthorized')
 if p=='/api/workspaces' and req.method=='GET':
  c=core.connect();rs=c.execute('select w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(u['id'],)).fetchall();c.close();return out({'workspaces':[core.workspace_to_json(x) for x in rs]})
 if p=='/api/workspaces' and req.method=='POST':
  b=await body(req)
  if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return err(400,'invalid slug')
  try:
   with core.tx() as c:
    x=c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(b['slug'],b.get('name',''),u['id']));w=x.lastrowid;g=core.create_channel_row(c,w,'general');core.add_workspace_member(c,w,u['id'],'owner');core.add_channel_member(c,g['id'],u['id'])
   return out({'workspace':core.workspace_to_json(core.get_workspace(b['slug'])),'general_channel':core.channel_to_json(core.get_channel(g['id']))},201)
  except:return err(409,'duplicate')
 m=re.match(r'/api/workspaces/([^/]+)(?:/(.*))?$',p)
 if m:
  w=core.get_workspace(m.group(1))
  if not w:return err(404,'not found')
  rest=m.group(2)
  if not rest:return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in core.connect().execute('select * from channels where workspace_id=?',(w['id'],)).fetchall()]})
  if rest=='members': 
   c=core.connect();rs=c.execute('select user_id,role from members where workspace_id=?',(w['id'],)).fetchall();c.close();return out({'members':[dict(x) for x in rs]})
  if rest=='channels' and req.method=='POST':
   b=await body(req);name=b.get('name','') if isinstance(b,dict) else ''
   if not re.fullmatch(r'[a-z0-9-]{1,32}',name):return err(400,'invalid name')
   c=core.connect()
   if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],name)).fetchone():c.close();return err(409,'duplicate')
   c.close()
   with core.tx() as c:
    x=core.create_channel_row(c,w['id'],name,bool(b.get('is_private')),b.get('topic',''));core.add_channel_member(c,x['id'],u['id'])
   return out({'channel':core.channel_to_json(core.get_channel(x['id']))},201)
 if re.match(r'/api/channels/\d+/messages$',p) and req.method=='POST':
  ch=core.get_channel(int(p.split('/')[3]))
  if not ch:return err(404,'not found')
  if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
  if ch['is_archived']:return err(423,'archived')
  b=await body(req);bo=b.get('body') if isinstance(b,dict) else None
  if not isinstance(bo,str) or not bo.strip():return err(400,'empty')
  return out({'message':core.create_message(u,ch['id'],bo,b.get('parent_id'))},201)
 if re.match(r'/api/channels/\d+/messages$',p) and req.method=='GET':
  ch=core.get_channel(int(p.split('/')[3]))
  if not ch:return err(404,'not found')
  return out({'messages':core.list_messages(ch['id']),'next_cursor':None})
 if p.startswith('/api/messages/') and ('/reactions' in p):
  mid=int(p.split('/')[3]); c=core.connect();m=c.execute('select * from messages where id=?',(mid,)).fetchone();c.close()
  if not m:return err(404,'not found')
  b=await body(req) if req.method=='POST' else {}
  emoji=(b.get('emoji') if isinstance(b,dict) else None) or (p.rsplit('/',1)[-1] if '/' in p else '')
  if not isinstance(emoji,str) or not emoji:return err(400,'invalid emoji')
  with core.tx() as c:
   if req.method=='POST': c.execute('insert or ignore into reactions values(?,?,?)',(mid,u['id'],emoji))
   else: c.execute('delete from reactions where message_id=? and user_id=? and emoji=?',(mid,u['id'],emoji))
   rs=c.execute('select emoji,count(*) n,group_concat(user_id) ids from reactions where message_id=? group by emoji',(mid,)).fetchall()
  return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rs]})
 m2=re.match(r'/api/messages/(\d+)(?:/replies)?$',p)
 if m2:
  mid=int(m2.group(1))
  if p.endswith('/replies'): 
   c=core.connect();r=c.execute('select channel_id from messages where id=?',(mid,)).fetchone();c.close();return out({'replies':core.list_messages(r['channel_id'],True) if r else [],'next_cursor':None})
  if req.method=='PATCH':
   b=await body(req);bo=b.get('body') if isinstance(b,dict) else None
   if not isinstance(bo,str) or not bo.strip():return err(400,'invalid body')
   s,d=core.edit_message(u,mid,bo);return out({'message':d},int(s)) if s=='200' else err(int(s),s)
  if req.method=='DELETE':
   s=core.delete_message(u,mid);return out({'deleted':True}) if s=='200' else err(int(s),s)
 if re.match(r'/api/channels/\d+/join$',p):
  ch=core.get_channel(int(p.split('/')[3]));return err(404,'not found') if not ch else out({'channel':core.join_channel(u,ch['id'])})
 if p.startswith('/api/channels/') and p.count('/')==3 and p.split('/')[3].isdigit():
  ch=core.get_channel(int(p.split('/')[3]))
  if not ch:return err(404,'not found')
  if req.method=='PATCH':
   b=await body(req)
   if not isinstance(b,dict) or not isinstance(b.get('topic'),str) or len(b.get('topic',''))>250:return err(400,'invalid topic')
   with core.tx() as c:c.execute('update channels set topic=? where id=?',(b['topic'],ch['id']))
   return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
  if req.method=='GET':return out({'channel':core.channel_to_json(ch)})
 if p=='/api/dms' and req.method=='POST':
  b=await body(req);return out({'channel_id':core.open_dm(u,int(b['user_id']))})
 return err(404,'not found')
async def channel_patch(req):
 u=auth(req)
 if not u:return err(401,'unauthorized')
 try: cid=int(req.match_info['id'])
 except:return err(400,'invalid channel')
 ch=core.get_channel(cid)
 if not ch:return err(404,'not found')
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return err(400,'invalid topic')
 with core.tx() as c:c.execute('update channels set topic=? where id=?',(b['topic'],cid))
 return out({'channel':core.channel_to_json(core.get_channel(cid))})
async def ws(req):
 u=core.user_for_token(req.query.get('token')); 
 if not u:return err(401,'unauthorized')
 w=web.WebSocketResponse();await w.prepare(req);subs={};sockets.add(w)
 async def pump():
  while not w.closed:
   for ch,n in list(subs.items()):
    batch=core.events_after(ch,n)
    for e in batch:
     await w.send_json(e)
     subs[ch]=e['event_id']
   await asyncio.sleep(.05)
 t=asyncio.create_task(pump())
 try:
  async for m in w:
   if m.type==WSMsgType.TEXT:
    x=json.loads(m.data);ch=x.get('channel_id')
    if x['type']=='subscribe':subs[ch]=core.head_event_id(ch);await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
    elif x['type']=='resume':subs[ch]=x.get('since_event_id',0);await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
 finally:t.cancel();sockets.discard(w)
 return w
async def home(req): return web.FileResponse('/app/static/index.html')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--node',type=int,default=0);a=ap.parse_args();app=web.Application();app['node']=a.node
 app.router.add_get('/api/ws',ws);app.router.add_patch('/api/channels/{id}',channel_patch);app.router.add_get('/',home);app.router.add_route('*','/api/messages/{id}/reactions',api);app.router.add_route('*','/api/messages/{id}/reactions/{emoji}',api);app.router.add_route('*','/api/{tail:.*}',api);web.run_app(app,host='127.0.0.1',port=8000+a.node)
if __name__=='__main__':main()
