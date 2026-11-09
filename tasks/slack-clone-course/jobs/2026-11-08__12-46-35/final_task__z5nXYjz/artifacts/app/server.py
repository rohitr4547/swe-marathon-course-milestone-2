import argparse,asyncio,json,os,re,uuid
from aiohttp import web,WSMsgType
import core
NODE=0
def j(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return j({'error':msg},status)
async def body(req):
 try:return await req.json()
 except:return None
def user(req):
 h=req.headers.get('Authorization','')
 token=h[7:] if h.startswith('Bearer ') else req.query.get('token')
 return core.user_for_token(token)
def auth(req):
 u=user(req); return u
def require(req):
 u=user(req)
 if not u: raise web.HTTPUnauthorized()
 return u
async def register(req):
 b=await body(req)
 if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid')
 with core.tx() as c:
  try:
   i=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'])).lastrowid
   tok=core.issue_token(c,i); r=c.execute('SELECT * FROM users WHERE id=?',(i,)).fetchone()
  except: return err(409,'duplicate')
 return j({'user':core.user_to_json(r),'token':tok},201)
async def login(req):
 b=await body(req); c=core.connect();r=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if isinstance(b,dict) else '',)).fetchone();c.close()
 if not r or not core.verify_password(b.get('password',''),r['password']):return err(401,'invalid')
 with core.tx() as c:t=core.issue_token(c,r['id'])
 return j({'user':core.user_to_json(r),'token':t})
async def me(req):
 u=auth(req)
 return j({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def health(req):return j({'status':'ok','node_id':NODE})
async def workspaces(req):
 u=require(req);c=core.connect(); rs=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],));x=[core.workspace_to_json(r) for r in rs];c.close();return j({'workspaces':x})
async def create_ws(req):
 u=require(req);b=await body(req)
 if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return err(400,'invalid')
 with core.tx() as c:
  try:
   w=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',''),u['id'])).lastrowid
  except:return err(409,'duplicate')
  core.add_workspace_member(c,w,u['id'],'owner');ch=core.create_channel_row(c,w,'general');core.add_channel_member(c,ch,u['id'])
  wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone();cr=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
 return j({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(cr)},201)
async def ws_detail(req):
 u=require(req);w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();chs=[dict(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],))];c.close();return j({'workspace':dict(w),'channels':chs,'read_state':{}})
async def create_ch(req):
 u=require(req);w=core.get_workspace(req.match_info['slug'])
 if not w or not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 b=await body(req);name=b.get('name','') if isinstance(b,dict) else ''
 if not re.fullmatch(r'[a-z0-9-]{1,32}',name):return err(400,'invalid')
 with core.tx() as c:
  if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone():return err(409,'duplicate')
  i=core.create_channel_row(c,w['id'],name,int(bool(b.get('is_private'))),b.get('topic',''));core.add_channel_member(c,i,u['id']);r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
 return j({'channel':dict(r)},201)
async def join(req):
 u=require(req);ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):
  w=core.connect().execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
  if w['join_mode']!='open':return err(403,'forbidden')
  with core.tx() as c:core.add_workspace_member(c,w['id'],u['id'])
 core.join_channel(u,ch['id']);return j({'channel':dict(ch)})
async def messages(req):
 u=require(req);cid=int(req.match_info['id']);ch=core.get_channel(cid)
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 if req.method=='GET':
  rows,_=core.list_messages(cid);return j({'messages':[core.message_to_json(x) for x in rows],'next_cursor':None})
 b=await body(req);text=b.get('body') if isinstance(b,dict) else None
 if not isinstance(text,str) or not text:return err(400,'empty')
 try:
  if text.startswith('/'):
   kind,val=core.run_slash(u,ch,text)
   if kind=='side':
    if isinstance(val,dict) and isinstance(val.get('channel'),dict):
     val['channel']=core.channel_to_json(val['channel'])
    return j(val,201)
   text=val
  if ch['is_archived']:return err(423,'archived')
  r=core.create_message(u,cid,text,b.get('parent_id'),b.get('file_ids'))
 except PermissionError:return err(403,'forbidden')
 except Exception as e:return err(400,str(e))
 if isinstance(r,dict) and isinstance(text,str):
  cmen=core.connect()
  crow=cmen.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  r['mentions']=core.resolve_mentions(cmen,text,crow,u['id'])
  cmen.close()
 return j({'message':r},201)
async def reactions(req):
 u=require(req);mid=int(req.match_info['id'])
 if req.method=='DELETE': emoji=req.match_info.get('emoji','')
 else:
  b=await body(req);emoji=b.get('emoji') if isinstance(b,dict) else ''
 if not isinstance(emoji,str) or not emoji:return err(400,'emoji')
 x=core.react(u,mid,emoji,req.method=='POST')
 if x is None:return err(404,'message')
 if x=='FORBIDDEN':return err(403,'forbidden')
 return j({'reactions':x})
async def upload_file(req):
 u=require(req)
 try:
  rd=await req.multipart(); part=await rd.next()
  if not part or part.name!='file': return err(400,'file')
  data=await part.read(decode=False)
  if len(data)>10485760:return err(413,'too large')
  fid=core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)
  c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
  return j({'file':core.file_to_json(r)},201)
 except Exception:return err(400,'file')
async def get_file(req):
 u=require(req);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
 return err(404,'file') if not r else j({'file':core.file_to_json(r)})
async def download_file(req):
 u=require(req);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
 if not r:return err(404,'file')
 try:data=open('/app/data/files/'+str(r['id']),'rb').read()
 except:return err(404,'file')
 return web.Response(body=data,content_type=r['content_type'],headers={'Content-Disposition':f'attachment; filename="{r["filename"]}"'})

async def groups(req):
 u=require(req);w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'workspace')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 handle=req.match_info.get('handle')
 if req.method=='GET':
  c=core.connect();x=[core.group_to_json(r) for r in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],))];c.close();return j({'groups':x})
 c=core.connect();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone() if handle else None;c.close()
 if req.method=='POST':
  b=await body(req);h=b.get('handle') if isinstance(b,dict) else None;ids=b.get('user_ids',[]) if isinstance(b,dict) else []
  if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',h) or not isinstance(ids,list):return err(400,'invalid')
  try:return j({'group':core.create_group(w['id'],h,b.get('name',''),ids,u['id'])},201)
  except FileExistsError:return err(409,'duplicate')
  except:return err(400,'invalid')
 if not g and req.method!='DELETE':return err(404,'group')
 if req.method=='DELETE':
  if g and g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
  core.delete_group(w['id'],handle);return j({'deleted':True})
 if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
 try:return j({'group':core.update_group(w['id'],handle,await body(req))})
 except: return err(400,'invalid')

async def dms(req):
 u=require(req);b=await body(req);oid=b.get('user_id') if isinstance(b,dict) else None
 if not isinstance(oid,int):return err(400,'user_id')
 c=core.connect();o=c.execute('SELECT * FROM users WHERE id=?',(oid,)).fetchone();c.close()
 if not o:return err(404,'user')
 return j({'channel_id':core.open_dm(u,oid)})
async def user_profile(req):
 u=require(req);c=core.connect();r=c.execute('SELECT * FROM users WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
 return err(404,'user') if not r else j({'user':core.user_to_json(r)})

async def profile(req):
 u=require(req)
 if req.method=='GET':return j({'user':core.user_to_json(u)})
 b=await body(req)
 if not isinstance(b,dict):return err(400,'invalid')
 try:r=core.update_profile(u,b)
 except:return err(400,'invalid')
 return j({'user':core.user_to_json(r)})
async def channels_members(req):
 u=require(req);ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 c=core.connect();x=[core.user_to_json(r) for r in c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],))];c.close();return j({'members':x})
async def ws_handler(req):
 u=auth(req)
 if not u:return err(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(req)
 cid=str(uuid.uuid4());core.touch_presence(cid,u['id']); subscribed={}; stop=False
 c0=core.connect();row=c0.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone();c0.close(); global_id=[row[0]]
 async def deliver():
  while not stop:
   try:
    core.touch_presence(cid,u['id'])
    for ch,since in list(subscribed.items()):
     for e in core.events_after(ch,since):
      await ws.send_json(e); subscribed[ch]=e['event_id']
    for gid,ge in core.global_events_after(global_id[0]):
     await ws.send_json(ge); global_id[0]=gid
    await asyncio.sleep(.05)
   except Exception: break
 task=asyncio.create_task(deliver())
 try:
  async for msg in ws:
   if msg.type==WSMsgType.TEXT:
    try:d=json.loads(msg.data)
    except:continue
    if d.get('type') in ('subscribe','resume'):
     try:ch=int(d['channel_id']);since=int(d.get('since_event_id',0))
     except:continue
     head=core.head_event_id(ch)
     subscribed[ch]=head if d.get('type')=='subscribe' else since
     await ws.send_json({'type':'subscribed' if d['type']=='subscribe' else 'resumed','channel_id':ch,'head_event_id':head})
     if d.get('type')=='resume':
      for e in core.events_after(ch,since):
       await ws.send_json(e);subscribed[ch]=e['event_id']
   elif msg.type in (WSMsgType.CLOSE,WSMsgType.ERROR):break
 finally:
  stop=True;task.cancel()
  try:await task
  except asyncio.CancelledError:pass
  core.drop_presence(cid)
  await asyncio.sleep(0)
 return ws

async def index(req):
 return web.Response(text=open('/app/static/index.html').read() if os.path.exists('/app/static/index.html') else '<h1>Huddle</h1>',content_type='text/html')
def app():
 a=web.Application();a.router.add_get('/',index);a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces/{slug}',ws_detail);a.router.add_post('/api/workspaces/{slug}/channels',create_ch);a.router.add_route('*','/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/channels/{id}/members',channels_members);a.router.add_route('*','/api/messages/{id}/reactions',reactions);a.router.add_route('DELETE','/api/messages/{id}/reactions/{emoji}',reactions);a.router.add_post('/api/dms',dms);a.router.add_route('*','/api/workspaces/{slug}/groups',groups);a.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',groups);a.router.add_post('/api/files',upload_file);a.router.add_get('/api/files/{id}',get_file);a.router.add_get('/api/files/{id}/download',download_file);a.router.add_route('*','/api/users/me',profile);a.router.add_get('/api/users/{id}',user_profile);a.router.add_get('/api/ws',ws_handler);return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(app(),host='127.0.0.1',port=8000+NODE)
