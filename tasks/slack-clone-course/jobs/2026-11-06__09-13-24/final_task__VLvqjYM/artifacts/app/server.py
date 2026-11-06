import asyncio, argparse, json, os, uuid, urllib.parse
from aiohttp import web, WSMsgType
import core

NODE=0
async def read_json(request):
    try:return await request.json()
    except:return None
def out(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return out({'error':msg},status)
def token(request):
    h=request.headers.get('Authorization','')
    return h[7:] if h.startswith('Bearer ') else None
def require_user(request):
    u=core.user_for_token(token(request))
    if not u: raise web.HTTPUnauthorized(text=json.dumps({'error':'unauthorized'}),content_type='application/json')
    return u
def user_id_path(request):
    try:return int(request.match_info['id'])
    except: raise web.HTTPNotFound()
def channel_or_404(i):
    c=core.get_channel(i)
    if not c: raise web.HTTPNotFound()
    return c
def access(u,ch):
    if not core.can_read_channel(u['id'],ch): raise web.HTTPForbidden()
def writable(ch):
    return not bool(ch['is_archived'])
async def register(request):
    b=await read_json(request)
    if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not isinstance(b.get('password'),str):return err(400,'invalid')
    if not __import__('re').fullmatch(r'[A-Za-z0-9_]+',b['username']) or len(b['password'])<8:return err(400,'invalid')
    try:
      with core.tx() as c:
       c.execute('INSERT INTO users(username,password,display_name) VALUES (?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
       u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
    except: return err(409,'duplicate')
    return out({'user':core.user_to_json(u),'token':t},201)
async def login(request):
 b=await read_json(request)
 if not isinstance(b,dict):return err(400,'invalid')
 c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username'),)).fetchone();c.close()
 if not u or not isinstance(b.get('password'),str) or not core.verify_password(b['password'],u['password']):return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t})
async def me(request):return out({'user':core.user_to_json(require_user(request))})
async def health(request):return out({'status':'ok','node_id':NODE})
async def user_get(request):
 require_user(request); c=core.connect();u=c.execute('SELECT * FROM users WHERE id=?',(user_id_path(request),)).fetchone();c.close()
 return out({'user':core.user_to_json(u)}) if u else err(404,'not found')
async def profile(request):
 u=require_user(request);b=await read_json(request)
 if not isinstance(b,dict):return err(400,'invalid')
 try:r=core.update_profile(u,b)
 except:return err(400,'invalid')
 return out({'user':core.user_to_json(r)})
async def ws_create(request):
 u=require_user(request);b=await read_json(request)
 if not isinstance(b,dict) or not isinstance(b.get('slug'),str) or not isinstance(b.get('name'),str) or not __import__('re').fullmatch(r'[a-z0-9-]{2,32}',b['slug']):return err(400,'invalid')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES (?,?,?)',(b['slug'],b['name'],u['id']));w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone();core.add_workspace_member(c,w['id'],u['id'],'owner');ch=core.create_channel_row(c,w['id'],'general');core.add_channel_member(c,ch['id'],u['id'])
 except:return err(409,'duplicate')
 return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
async def ws_list(request):
 u=require_user(request);c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return out({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def ws_detail(request):
 u=require_user(request);w=core.get_workspace(request.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();chs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
 return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in chs]})
async def members(request):
 u=require_user(request);w=core.get_workspace(request.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT m.*,u.* FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
 return out({'members':[{'user_id':x['user_id'],'role':x['role'],**core.user_to_json(x)} for x in rs]})
async def channel_create(request):
 u=require_user(request);w=core.get_workspace(request.match_info['slug']);b=await read_json(request)
 if not w:return err(404,'not found')
 role=core.member_role(w['id'],u['id'])
 if not role:return err(403,'forbidden')
 if not isinstance(b,dict) or not isinstance(b.get('name'),str) or not __import__('re').fullmatch(r'[a-z0-9-]{1,32}',b['name']):return err(400,'invalid')
 private=bool(b.get('is_private',False))
 if private and role not in ('owner','admin'):return err(403,'forbidden')
 c=core.connect();exists=c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone();c.close()
 if exists:return err(409,'duplicate')
 with core.tx() as c:
  ch=core.create_channel_row(c,w['id'],b['name'],private,b.get('topic',''));core.add_channel_member(c,ch['id'],u['id'])
 return out({'channel':core.channel_to_json(ch)},201)
async def join(request):
 u=require_user(request);ch=channel_or_404(int(request.match_info['id']))
 if ch['workspace_id']:
  w=core.get_workspace(ch['workspace_id'])
  if ch['is_private'] and not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 core.join_channel(u,ch['id']);return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def leave(request):
 u=require_user(request);ch=channel_or_404(int(request.match_info['id']))
 with core.tx() as c:core.remove_channel_member(c,ch['id'],u['id'])
 return out({'channel':core.channel_to_json(ch)})
async def ch_members(request):
 u=require_user(request);ch=channel_or_404(int(request.match_info['id']));access(u,ch);c=core.connect();rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close();return out({'members':[core.user_to_json(x) for x in rs]})
async def ch_patch(request):
 u=require_user(request);ch=channel_or_404(int(request.match_info['id']));b=await read_json(request)
 if not isinstance(b,dict) or not isinstance(b.get('topic',''),str) or len(b.get('topic',''))>250:return err(400,'invalid')
 with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),ch['id']))
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def archive(request):
 u=require_user(request);ch=channel_or_404(int(request.match_info['id']));cc=core.connect();w=cc.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();cc.close()
 if w['owner_id']!=u['id']:return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(0 if request.path.endswith('/unarchive') else 1,ch['id']))
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def post_message(request):
 u=require_user(request);ch=channel_or_404(int(request.match_info['id']));b=await read_json(request)
 if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty')
 body=b['body']
 if body not in ('/archive','/unarchive'): access(u,ch)
 if body.startswith('/'):
  if body.startswith('//'):body=body[1:]
  elif body.startswith('/me'):
   if len(body)<=3 or not body[3:].strip():return err(400,'invalid')
  elif body.startswith('/shrug'):body=(body[6:].strip()+' ¯\\_(ツ)_/¯').strip()
  elif body.startswith('/topic '):
   with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(body[7:],ch['id']))
   return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
  elif body.startswith('/invite @'):
   c=core.connect();x=c.execute('SELECT * FROM users WHERE username=?',(body[9:].strip(),)).fetchone();c.close()
   if not x:return err(404,'not found')
   with core.tx() as c:core.add_channel_member(c,ch['id'],x['id'])
   return out({'message':None,'invited_user':core.user_to_json(x)},201)
  elif body in ('/archive','/unarchive'):
   cc=core.connect();w=cc.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();cc.close()
   if not w or int(w['owner_id']) != int(u['id']):
    return err(403,'forbidden')
   with core.tx() as c:
    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if body=='/archive' else 0,ch['id']))
   return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
  else:return err(400,'unknown command')
 if not writable(ch): return err(423,'archived')
 try:m=core.create_message(u,ch['id'],body,b.get('parent_id'),b.get('file_ids'))
 except:return err(400,'invalid')
 return out({'message':m},201)
async def messages(request):
 u=require_user(request);ch=channel_or_404(int(request.match_info['id']));access(u,ch)
 try:n,cur=core.parse_paging(request.query,'messages')
 except:return err(400,'cursor')
 rs,nc=core.list_messages(ch['id'],n,cur);return out({'messages':[core.message_to_json(x) for x in rs],'next_cursor':nc})
async def replies(request):
 u=require_user(request);c=core.connect();p=c.execute('SELECT * FROM messages WHERE id=?',(int(request.match_info['id']),)).fetchone();c.close()
 if not p:return err(404,'not found')
 ch=channel_or_404(p['channel_id']);access(u,ch)
 try:n,cur=core.parse_paging(request.query,'replies')
 except:return err(400,'cursor')
 rs,nc=core.list_replies(p['id'],n,cur);return out({'replies':[core.message_to_json(x) for x in rs],'next_cursor':nc})
async def reactions(request):
 u=require_user(request);mid=int(request.match_info['id']);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 access(u,channel_or_404(m['channel_id']))
 if request.method=='POST':
  b=await read_json(request);em=b.get('emoji') if isinstance(b,dict) else None
  if not isinstance(em,str) or not em:return err(400,'invalid')
  return out({'reactions':core.react(u,mid,em,True)})
 em=urllib.parse.unquote(request.match_info['emoji']);return out({'reactions':core.react(u,mid,em,False)})
async def dms(request):
 u=require_user(request);b=await read_json(request)
 if not isinstance(b,dict) or not isinstance(b.get('user_id'),int):return err(400,'invalid')
 c=core.connect();x=c.execute('SELECT * FROM users WHERE id=?',(b['user_id'],)).fetchone();c.close()
 if not x:return err(404,'not found')
 return out({'channel_id':core.open_dm(u,x['id'])})
async def files(request):
 u=require_user(request)
 try:
  ct=request.headers.get('Content-Type','')
  if 'boundary=' not in ct:return err(400,'invalid')
  boundary=ct.split('boundary=',1)[1].split(';',1)[0].strip().strip('"').encode()
  raw=await request.read()
  chunks=raw.split(b'--'+boundary)
  if len(chunks)<3:return err(400,'invalid')
  part=chunks[1]
  if part.startswith(b'\r\n'):part=part[2:]
  head,data=part.split(b'\r\n\r\n',1)
  if data.endswith(b'\r\n'):data=data[:-2]
  hs=head.decode('latin1')
  import re
  fm=re.search(r'filename="([^"]*)"',hs)
  if not fm:return err(400,'invalid')
  cm=re.search(r'(?im)^Content-Type:\s*([^\r\n]+)',hs)
  r=core.save_file(u,fm.group(1),cm.group(1).strip() if cm else 'application/octet-stream',data)
 except OverflowError:return err(413,'too large')
 except Exception as ex:
  open('/tmp/upload-error','a').write(repr(ex)+'\\n')
  return err(400,'invalid')
 return out({'file':core.file_to_json(r)},201)
async def file_get(request):
 require_user(request);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(request.match_info['id']),)).fetchone();c.close()
 return out({'file':core.file_to_json(r)}) if r else err(404,'not found')
async def file_download(request):
 require_user(request);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(request.match_info['id']),)).fetchone();c.close()
 if not r:return err(404,'not found')
 return web.Response(body=open(f'/app/data/files/{r["id"]}','rb').read(),content_type=r['content_type'],headers={'Content-Disposition':f'attachment; filename="{r["filename"]}"'})
async def groups(request):
 u=require_user(request);w=core.get_workspace(request.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 if request.method=='GET':return out({'groups':[core.group_to_json(x) for x in rs]})
 b=await read_json(request)
 if not isinstance(b,dict) or not isinstance(b.get('handle'),str) or not __import__('re').fullmatch(r'[a-z0-9-]{1,32}',b['handle']) or not isinstance(b.get('user_ids'),list):return err(400,'invalid')
 for x in b['user_ids']:
  if not core.member_role(w['id'],x):return err(400,'nonmember')
 try:r=core.create_group(w['id'],u['id'],b['handle'],b.get('name',''),b['user_ids'])
 except:return err(409,'duplicate')
 return out({'group':core.group_to_json(r)},201)
async def group_modify(request):
 u=require_user(request);w=core.get_workspace(request.match_info['slug']);h=request.match_info['handle'];c=core.connect();r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone();c.close()
 if not r:
  if request.method=='DELETE': return out({'deleted':True})
  return err(404,'not found')
 if r['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
 if request.method=='DELETE':return out(core.delete_group(w['id'],h))
 b=await read_json(request);r=core.update_group(w['id'],h,b or {});return out({'group':core.group_to_json(r)})
async def ws_handler(request):
 u=core.user_for_token(request.query.get('token','')); 
 if not u:return err(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(request);conn=uuid.uuid4().hex;core.touch_presence(conn,u['id']);subs={};glob=0
 async def poll():
  nonlocal glob
  while not ws.closed:
   for ch,s in list(subs.items()):
    for e in core.events_after(ch,s):await ws.send_json(e);subs[ch]=e['event_id']
   for i,e in core.globals_after(glob):await ws.send_json(e);glob=i
   core.touch_presence(conn,u['id']);await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for msg in ws:
   if msg.type==WSMsgType.TEXT:
    b=json.loads(msg.data);ch=b.get('channel_id')
    if b.get('type')=='subscribe':subs[ch]=core.head_event_id(ch);await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':subs[ch]})
    elif b.get('type')=='resume':subs[ch]=b.get('since_event_id',0);await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':core.head_event_id(ch)})
 finally:task.cancel();core.drop_presence(conn)
 return ws
async def index(request):
 return web.Response(text=open('/app/static/index.html').read(),content_type='text/html')
def app():
 a=web.Application(client_max_size=12*1024*1024)
 routes=[('POST','/api/auth/register',register),('POST','/api/auth/login',login),('GET','/api/auth/me',me),('GET','/api/health',health),('GET','/api/users/{id}',user_get),('PATCH','/api/users/me',profile),('POST','/api/workspaces',ws_create),('GET','/api/workspaces',ws_list),('GET','/api/workspaces/{slug}',ws_detail),('GET','/api/workspaces/{slug}/members',members),('POST','/api/workspaces/{slug}/channels',channel_create),('POST','/api/channels/{id}/join',join),('POST','/api/channels/{id}/leave',leave),('GET','/api/channels/{id}/members',ch_members),('PATCH','/api/channels/{id}',ch_patch),('POST','/api/channels/{id}/archive',archive),('POST','/api/channels/{id}/unarchive',archive),('POST','/api/channels/{id}/messages',post_message),('GET','/api/channels/{id}/messages',messages),('GET','/api/messages/{id}/replies',replies),('POST','/api/messages/{id}/reactions',reactions),('DELETE','/api/messages/{id}/reactions/{emoji}',reactions),('POST','/api/dms',dms),('POST','/api/files',files),('GET','/api/files/{id}',file_get),('GET','/api/files/{id}/download',file_download),('GET','/api/workspaces/{slug}/groups',groups),('POST','/api/workspaces/{slug}/groups',groups),('PATCH','/api/workspaces/{slug}/groups/{handle}',group_modify),('DELETE','/api/workspaces/{slug}/groups/{handle}',group_modify),('GET','/api/ws',ws_handler),('GET','/',index)]
 for method,path,fn in routes:a.router.add_route(method,path,fn)
 return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);args=p.parse_args();NODE=args.node;web.run_app(app(),host='127.0.0.1',port=8000+NODE)
