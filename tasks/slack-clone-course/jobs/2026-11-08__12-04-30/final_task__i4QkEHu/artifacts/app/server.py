import asyncio,json,os,argparse,urllib.parse,re,secrets
from aiohttp import web,WSMsgType
import core
def out(x,s=200): return web.json_response(x,status=s)
def err(s,m): return out({'error':m},s)
def user(r):
 h=r.headers.get('Authorization',''); return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
async def body(r):
 try:return await r.json()
 except:return None
async def health(r):return out({'status':'ok','node_id':r.app['node']})
async def reg(r):
 d=await body(r)
 if not isinstance(d,dict) or not isinstance(d.get('username'),str) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid')
 with core.tx() as c:
  try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
  except:return err(409,'duplicate')
  u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone();t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t},201)
async def login(r):
 d=await body(r);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username','') if isinstance(d,dict) else '',)).fetchone();c.close()
 if not u or not core.verify_password(d.get('password',''),u['password']):return err(401,'invalid')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=user(r);return out({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspaces(r):
 u=user(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();a=[dict(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN wmembers m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))];c.close();return out({'workspaces':a})
async def create_workspace(r):
 u=user(r);d=await body(r)
 if not u:return err(401,'unauthorized')
 if not isinstance(d,dict) or not isinstance(d.get('slug'),str) or not isinstance(d.get('name'),str):return err(400,'invalid')
 with core.tx() as c:
  try:c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']))
  except:return err(409,'duplicate')
  w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone();c.execute("INSERT INTO channels(workspace_id,name) VALUES(?, 'general')",(w['id'],));ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone();core.add_workspace_member(c,w['id'],u['id'],'owner')
 return out({'workspace':dict(w),'general_channel':core.channel_to_json(ch)},201)
async def workspace_detail(r):
 u=user(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w or not core.member_role(w['id'],u['id']):return err(404,'not found')
 c=core.connect();chs=[core.channel_to_json(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],))];c.close()
 return out({'workspace':dict(w),'channels':chs})
async def channel_members(r):
 u=user(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 c=core.connect();a=[core.user_to_json(x) for x in c.execute('SELECT u.* FROM users u JOIN members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],))];c.close()
 return out({'members':a})
async def create_channel(r):
 u=user(r);w=core.get_workspace(r.match_info['slug']);d=await body(r)
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 with core.tx() as c:
  try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d.get('name'),int(bool(d.get('is_private'))),d.get('topic','')))
  except:return err(409,'duplicate')
  ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone();core.add_channel_member(c,ch['id'],u['id'])
 return out({'channel':core.channel_to_json(ch)},201)
async def join(r):
 u=user(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if ch['is_private'] and not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 with core.tx() as c:
  core.add_channel_member(c,ch['id'],u['id'])
  if ch['workspace_id']:core.add_workspace_member(c,ch['workspace_id'],u['id'])
 return out({'channel':core.channel_to_json(ch)})
async def post(r):
 u=user(r);ch=core.get_channel(r.match_info['id']);d=await body(r)
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 if ch['is_archived'] and (not isinstance(d,dict) or not (isinstance(d.get('body'),str) and d['body'].startswith('/unarchive'))):return err(423,'archived')
 if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'empty')
 if 'file_ids' in d and (not isinstance(d['file_ids'],list) or any(not isinstance(x,int) for x in d['file_ids'])):return err(400,'file_ids')
 if d['body'].startswith('/') and not d['body'].startswith('//'):
  bits=d['body'].split(None,1);cmd=bits[0];text=bits[1].strip() if len(bits)>1 else ''
  if cmd=='/me':
   if not text:return err(400,'text required')
  elif cmd=='/shrug':
   d['body']=(text+' ' if text else '')+'¯\\_(ツ)_/¯'
  elif cmd=='/topic':
   if not text:return err(400,'text required')
   with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(text,ch['id']))
   return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
  elif cmd in ('/archive','/unarchive'):
   if ch['workspace_id'] is None or core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
   with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if cmd=='/archive' else 0,ch['id']))
   return out({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
  elif cmd=='/invite':
   if not re.fullmatch(r'@[A-Za-z0-9_]+',text):return err(400,'user')
   c=core.connect();target=c.execute('SELECT * FROM users WHERE username=?',(text[1:],)).fetchone();c.close()
   if not target:return err(404,'not found')
   with core.tx() as c:core.add_channel_member(c,ch['id'],target['id'])
   return out({'message':None,'invited_user':core.user_to_json(target)},201)
  else:return err(400,'unknown command')
 if d['body'].startswith('//'):d['body']=d['body'][1:]
 if isinstance(d.get('file_ids'),list):
  c=core.connect(); ok=len(set(d['file_ids']))==len(d['file_ids']) and all(c.execute('SELECT 1 FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(x,u['id'])).fetchone() for x in d['file_ids']); c.close()
  if not ok:return err(400,'file_ids')
 with core.tx() as c:
  c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],d['body'],d.get('parent_id'),core.now_iso()));m=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone()
  for fid in d.get('file_ids',[]): c.execute('UPDATE files SET message_id=? WHERE id=?',(m['id'],fid))
  mentions=set()
  for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)',d['body']):
   if name=='channel':
    mentions.update(x[0] for x in c.execute('SELECT user_id FROM members WHERE channel_id=?',(ch['id'],)))
   elif name=='here':
    mentions.update(core.present_user_ids())
   else:
    u2=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
    if u2 and c.execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(ch['id'],u2[0])).fetchone():mentions.add(u2[0])
    g=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(ch['workspace_id'],name)).fetchone() if ch['workspace_id'] else None
    if g:mentions.update(x[0] for x in c.execute('SELECT gm.user_id FROM group_members gm JOIN members m ON m.user_id=gm.user_id WHERE gm.group_id=? AND m.channel_id=?',(g[0],ch['id'])))
  mentions.discard(u['id'])
  core.emit_event(c,ch['id'],'message.created',{'message':core.message_to_json(m)})
 mj=core.message_to_json(m);mj['mentions']=sorted(mentions) if 'mentions' in locals() else [];return out({'message':mj},201)
async def messages(r):
 u=user(r);ch=core.get_channel(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 c=core.connect();a=[core.message_to_json(x) for x in c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC',(ch['id'],))];c.close();return out({'messages':a,'next_cursor':None})
async def react(r):
 u=user(r);mid=int(r.match_info['id'])
 if not u:return err(401,'unauthorized')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=(SELECT channel_id FROM messages WHERE id=?)',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 if r.method=='POST':
  d=await body(r);emoji=d.get('emoji') if isinstance(d,dict) else None
  if not isinstance(emoji,str) or not emoji:return err(400,'emoji')
 else:emoji=urllib.parse.unquote(r.match_info['emoji'])
 with core.tx() as c:
  if r.method=='POST':
   before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone();c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
   if not before:core.emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
  else:
   before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone();c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
   if before:core.emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
 c=core.connect();a=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,))];c.close();return out({'reactions':a})
async def profile_update(r):
 u=user(r)
 if not u:return err(401,'unauthorized')
 d=await body(r); allowed={'display_name':(1,80),'timezone':None,'avatar_url':(0,512),'status_text':(0,140),'status_emoji':(0,32)}
 if not isinstance(d,dict):return err(400,'invalid')
 for k,v in d.items():
  if k not in allowed or not isinstance(v,str) or (allowed[k] and not allowed[k][0]<=len(v)<=allowed[k][1]):return err(400,'invalid')
 if 'timezone' in d:
  try: __import__('zoneinfo').ZoneInfo(d['timezone'])
  except:return err(400,'timezone')
 with core.tx() as c:
  if d:
   c.execute('UPDATE users SET '+','.join(k+'=?' for k in d)+' WHERE id=?',tuple(d.values())+(u['id'],))
   fresh=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
   core.emit_global(c,{'type':'user.updated','user':core.user_to_json(fresh)})
 return out({'user':core.user_obj(u['id'])})
async def profile_route(r):
 u=user(r)
 if not u:return err(401,'unauthorized')
 x=core.user_obj(int(r.match_info['id']))
 return out({'user':x}) if x else err(404,'not found')
async def dms(r):
 u=user(r)
 if not u:return err(401,'unauthorized')
 d=await body(r)
 if not isinstance(d,dict) or not isinstance(d.get('user_id'),int):return err(400,'user_id')
 c=core.connect();o=c.execute('SELECT id FROM users WHERE id=?',(d['user_id'],)).fetchone()
 if not o:c.close();return err(404,'not found')
 a,b=sorted((u['id'],d['user_id']));x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone();c.close()
 if x:return out({'channel_id':x[0]})
 with core.tx() as c:
  c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)');ch=c.execute('SELECT id FROM channels WHERE id=last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,ch));c.execute('INSERT INTO members VALUES(?,?),(?,?)',(ch,a,ch,b))
 return out({'channel_id':ch})
async def upload(r):
 u=user(r)
 if not u:return err(401,'unauthorized')
 try:
  q=await r.multipart();f=await q.next();data=await f.read()
 except:return err(400,'file')
 if len(data)>10*1024*1024:return err(413,'too large')
 with core.tx() as c:
  c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],f.filename or 'file',f.headers.get('Content-Type','application/octet-stream'),len(data),core.now_iso()));fid=c.execute('SELECT id FROM files WHERE id=last_insert_rowid()').fetchone()[0]
 os.makedirs('/app/data/files',exist_ok=True);open('/app/data/files/'+str(fid),'wb').write(data)
 c=core.connect();x=dict(c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone());c.close();return out({'file':x},201)
async def fileget(r):
 u=user(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 return out({'file':dict(x)}) if x else err(404,'not found')
async def download(r):
 u=user(r)
 if not u:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
 if not x:return err(404,'not found')
 return web.Response(body=open('/app/data/files/'+str(x['id']),'rb').read(),content_type=x['content_type'],headers={'Content-Disposition':'attachment; filename="'+x['filename']+'"'})
async def groups(r):
 u=user(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=? ORDER BY id',(w['id'],)).fetchall()
 result=[]
 for g in rows:
  ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]
  result.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids})
 c.close()
 if r.method=='GET':return out({'groups':result})
 d=await body(r)
 if not isinstance(d,dict) or not isinstance(d.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d.get('handle','')) or not isinstance(d.get('name'),str) or not isinstance(d.get('user_ids'),list):return err(400,'invalid')
 with core.tx() as c:
  if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],d['handle'])).fetchone():return err(409,'duplicate')
  if not all(isinstance(x,int) and c.execute('SELECT 1 FROM wmembers WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone() for x in d['user_ids']):return err(400,'members')
  c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],d['handle'],d['name'],u['id']));g=c.execute('SELECT * FROM groups_ WHERE id=last_insert_rowid()').fetchone()
  for x in d['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],x))
 return out({'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':d['user_ids']}},201)
async def group_edit(r):
 u=user(r);w=core.get_workspace(r.match_info['slug'])
 if not u:return err(401,'unauthorized')
 if not w:return err(404,'not found')
 c=core.connect();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone();c.close()
 if not g and r.method=='DELETE':return out({'deleted':True})
 if not g:return err(404,'not found')
 if core.member_role(w['id'],u['id']) not in ('owner',) and g['creator_id']!=u['id']:return err(403,'forbidden')
 if r.method=='DELETE':
  with core.tx() as c:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
  return out({'deleted':True})
 d=await body(r)
 with core.tx() as c:
  if 'name' in d:c.execute('UPDATE groups_ SET name=? WHERE id=?',(d['name'],g['id']))
  if 'user_ids' in d:
   c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
   for x in d['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],x))
 c=core.connect();g=c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone();ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))];c.close()
 return out({'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids}})
async def auth404(r):
 return err(401,'unauthorized') if not user(r) else err(404,'not found')
async def ws(r):
 u=core.user_for_token(r.query.get('token',''))
 if not u:return err(401,'unauthorized')
 w=web.WebSocketResponse();await w.prepare(r);last={};running=True;conn_id=secrets.token_urlsafe(12);core.touch_presence(conn_id,u['id'])
 async def poll():
  global_seen=0
  while running:
   try:
    x=core.globals_after(global_seen)
    for ident,g in x:
     await w.send_json(g)
     global_seen=ident
    for ch,n in list(last.items()):
     for e in core.events_after(ch,n):
      await w.send_json(e);last[ch]=e['event_id']
   except Exception: pass
   await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for z in w:
   if z.type==WSMsgType.TEXT:
    d=json.loads(z.data)
    if d.get('type') in ('subscribe','resume'):
     ch=int(d['channel_id']);head=core.head_event_id(ch)
     last[ch]=head if d['type']=='subscribe' else int(d.get('since_event_id',0))
     await w.send_json({'type':'subscribed' if d['type']=='subscribe' else 'resumed','channel_id':ch,'head_event_id':head})
     if d['type']=='resume':
      for e in core.events_after(ch,last[ch]):
       await w.send_json(e);last[ch]=e['event_id']
 except Exception: pass
 running=False;task.cancel();core.drop_presence(conn_id)
 return w
def make(n):
 a=web.Application();a['node']=n
 for meth,path,fn in [('GET','/api/health',health),('POST','/api/auth/register',reg),('POST','/api/auth/login',login),('GET','/api/auth/me',me),('GET','/api/users/{id}',profile_route),('PATCH','/api/users/me',profile_update),('GET','/api/workspaces',workspaces),('GET','/api/workspaces/{slug}',workspace_detail),('GET','/api/channels/{id}/members',channel_members),('POST','/api/workspaces',create_workspace),('POST','/api/workspaces/{slug}/channels',create_channel),('POST','/api/channels/{id}/join',join),('POST','/api/channels/{id}/messages',post),('GET','/api/channels/{id}/messages',messages),('POST','/api/messages/{id}/reactions',react),('DELETE','/api/messages/{id}/reactions/{emoji}',react),('GET','/api/ws',ws),('POST','/api/dms',dms),('POST','/api/files',upload),('GET','/api/files/{id}',fileget),('GET','/api/files/{id}/download',download),('GET','/api/workspaces/{slug}/groups',groups),('POST','/api/workspaces/{slug}/groups',groups),('PATCH','/api/workspaces/{slug}/groups/{handle}',group_edit),('DELETE','/api/workspaces/{slug}/groups/{handle}',group_edit)]:getattr(a.router,'add_'+meth.lower())(path,fn)
 return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int);n=p.parse_args().node;web.run_app(make(n),host='127.0.0.1',port=8000+n)
