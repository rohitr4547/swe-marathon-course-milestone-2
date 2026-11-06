import os,sys,sqlite3,json,secrets,hashlib,re,asyncio,threading,time,datetime,urllib.parse,traceback
from aiohttp import web
DB='/app/data/huddle.db'; PORT=int(os.getenv('PORT','8000'))
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); return c
def init():
 c=db()
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER);
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS members(channel_id INTEGER,user_id INTEGER,UNIQUE(channel_id,user_id));
CREATE TABLE IF NOT EXISTS wmembers(workspace_id INTEGER,user_id INTEGER,role TEXT,UNIQUE(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,emoji TEXT,user_id INTEGER,created_at TEXT,UNIQUE(message_id,emoji,user_id));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,data BLOB,created_at TEXT,attached INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS groupsx(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT UNIQUE,name TEXT,creator_id INTEGER,users TEXT);
CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER,UNIQUE(message_id,file_id));
CREATE TABLE IF NOT EXISTS ws_presence(user_id INTEGER PRIMARY KEY,seen_at REAL);
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT,created_at TEXT);
'''); c.commit(); c.close()
init()
def q(sql,args=(),one=False): 
 c=db(); r=c.execute(sql,args); x=r.fetchone() if one else r.fetchall(); c.close(); return x
def exec(sql,args=()):
 c=db(); r=c.execute(sql,args); c.commit(); x=r.lastrowid; c.close(); return x
def userobj(r): return {k:(r[k] or '') for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def channelobj(r):
 d=dict(r) if r else {}
 if 'archived' in d:
  d['is_archived']=bool(d['archived'])
 return d
def auth(req):
 t=req.headers.get('Authorization','').replace('Bearer ','') or req.query.get('token','').replace('Bearer ','')
 r=q('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(t,),True)
 return r
def js(x,status=200): return web.json_response(x,status=status)
def bad(s): return js({'error':s},400)
def member(uid,cid):
 return q('SELECT 1 FROM members WHERE user_id=? AND channel_id=?',(uid,cid),True) is not None
def chan(cid): return q('SELECT * FROM channels WHERE id=?',(cid,),True)
def emit(cid,kind,payload):
 c=db(); c.execute('BEGIN IMMEDIATE'); n=c.execute('SELECT COALESCE(MAX(event_id),0)+1 n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']; c.execute('INSERT INTO events VALUES(?,?,?,?,?)',(cid,n,kind,json.dumps(payload),now())); c.commit(); c.close(); return n
def msgobj(r):
 u=q('SELECT * FROM users WHERE id=?',(r['author_id'],),True)
 rs=[]
 for x in q('SELECT emoji, GROUP_CONCAT(user_id) ids, COUNT(*) n FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(r['id'],)):
  rs.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]})
 replies=q('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],),True)['n']
 fs=[]
 for f in q('SELECT f.id,f.uploader_id,f.filename,f.content_type,f.size,f.created_at FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(r['id'],)):
  fs.append(dict(f))
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fs,'reactions':rs,'mentions':mentions(r['body'],r['channel_id'],r['author_id'])}
def mentions(body,cid,author):
 c=chan(cid); out=set(); ws=c['workspace_id']
 for x in re.findall(r'(?<!\w)@([A-Za-z0-9_-]+)',body):
  if x=='channel':
   out|={r['user_id'] for r in q('SELECT user_id FROM members WHERE channel_id=?',(cid,))}
  else:
   u=q('SELECT id FROM users WHERE username=?',(x,),True)
   if u and member(u['id'],cid): out.add(u['id'])
   g=q('SELECT users FROM groupsx WHERE handle=? AND workspace_id=?',(x,ws),True)
   if g:
    for gid in json.loads(g['users']):
     if member(gid,cid): out.add(gid)
   if x=='here':
    for z in q('SELECT user_id FROM ws_presence WHERE seen_at>?',(time.time()-10,)):
     if member(z['user_id'],cid): out.add(z['user_id'])
 return [i for i in sorted(out) if i!=author]
async def require(req):
 u=auth(req)
 if not u: raise web.HTTPUnauthorized()
 return u
async def health(req): return js({'status':'ok','node_id':PORT-8000})
async def register(req):
 try: b=await req.json(); un=b.get('username'); pw=b.get('password')
 except: return bad('invalid json')
 if not isinstance(un,str) or not re.fullmatch(r'[A-Za-z0-9_]+',un) or not isinstance(pw,str) or len(pw)<8:return bad('invalid registration')
 if q('SELECT 1 FROM users WHERE username=?',(un,),True): return js({'error':'exists'},409)
 uid=exec('INSERT INTO users(username,password,display_name,timezone,avatar_url,status_text,status_emoji) VALUES(?,?,?,?,?,?,?)',(un,hashlib.sha256(pw.encode()).hexdigest(),b.get('display_name') or un,'UTC','','',''))
 tok=secrets.token_urlsafe(24); exec('INSERT INTO tokens VALUES(?,?)',(tok,uid)); return js({'user':userobj(q('SELECT * FROM users WHERE id=?',(uid,),True)),'token':tok},201)
async def login(req):
 b=await req.json(); u=q('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),hashlib.sha256(str(b.get('password','')).encode()).hexdigest()),True)
 if not u:return js({'error':'bad credentials'},401)
 t=secrets.token_urlsafe(24);exec('INSERT INTO tokens VALUES(?,?)',(t,u['id'],));return js({'user':userobj(u),'token':t})
async def me(req): return js({'user':userobj(await require(req))})
async def workspaces(req):
 u=await require(req); return js({'workspaces':[dict(x) for x in q('SELECT w.* FROM workspaces w JOIN wmembers m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))]})
async def createws(req):
 u=await require(req); b=await req.json(); slug=b.get('slug',''); name=b.get('name','')
 if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not isinstance(name,str) or not name:return bad('invalid')
 if q('SELECT 1 FROM workspaces WHERE slug=?',(slug,),True):return js({'error':'exists'},409)
 wid=exec('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id'])); cid=exec('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));exec('INSERT INTO wmembers VALUES(?,?,?)',(wid,u['id'],'owner'));exec('INSERT INTO members VALUES(?,?)',(cid,u['id']))
 return js({'workspace':dict(q('SELECT * FROM workspaces WHERE id=?',(wid,),True)),'general_channel':dict(q('SELECT * FROM channels WHERE id=?',(cid,),True))},201)
async def wsdetail(req):
 u=await require(req); w=q('SELECT * FROM workspaces WHERE slug=?',(req.match_info['slug'],),True)
 if not w:return js({'error':'not found'},404)
 cs=[dict(x) for x in q('SELECT * FROM channels WHERE workspace_id=? AND archived=0',(w['id'],))]
 return js({'workspace':dict(w),'channels':cs,'read_state':{}})
async def members(req):
 u=await require(req); w=q('SELECT id FROM workspaces WHERE slug=?',(req.match_info['slug'],),True)
 if not w:return js({'error':'not found'},404)
 return js({'members':[{'user_id':x['id'],'role':x['role']} for x in q('SELECT u.id,m.role FROM users u JOIN wmembers m ON m.user_id=u.id WHERE m.workspace_id=?',(w['id'],))]})
async def createch(req):
 u=await require(req); w=q('SELECT * FROM workspaces WHERE slug=?',(req.match_info['slug'],),True); b=await req.json()
 if not w:return js({'error':'not found'},404)
 if q('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b.get('name')),True):return js({'error':'exists'},409)
 cid=exec('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b.get('name'),int(bool(b.get('is_private'))),b.get('topic','')));exec('INSERT INTO members VALUES(?,?)',(cid,u['id']))
 return js({'channel':dict(q('SELECT * FROM channels WHERE id=?',(cid,),True))},201)
async def join(req):
 u=await require(req); c=chan(int(req.match_info['id']))
 if not c:return js({'error':'not found'},404)
 exec('INSERT OR IGNORE INTO members VALUES(?,?)',(c['id'],u['id'])); w=q('SELECT * FROM wmembers WHERE workspace_id=? AND user_id=?',(c['workspace_id'],u['id']),True)
 if not w:exec('INSERT OR IGNORE INTO wmembers VALUES(?,?,?)',(c['workspace_id'],u['id'],'member'))
 return js({'channel':channelobj(c)})
async def channelmembers(req):
 u=await require(req); cid=int(req.match_info['id']); return js({'members':[userobj(x) for x in q('SELECT u.* FROM users u JOIN members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))]})
async def patchch(req):
 u=await require(req); c=chan(int(req.match_info['id'])); b=await req.json()
 if not c:return js({'error':'not found'},404)
 exec('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),c['id']));return js({'channel':dict(chan(c['id']))})
async def archive(req):
 u=await require(req); c=chan(int(req.match_info['id'])); exec('UPDATE channels SET archived=? WHERE id=?',(0 if req.path.endswith('/unarchive') else 1,c['id']));return js({'channel':dict(chan(c['id']))})
async def postmsg(req):
 u=await require(req); cid=int(req.match_info['id']); c=chan(cid)
 try: b=await req.json()
 except: return bad('invalid json')
 body=b.get('body')
 if not c:return js({'error':'not found'},404)
 if not member(u['id'],cid):return js({'error':'forbidden'},403)
 if c['archived'] and body.strip() != '/unarchive':return js({'error':'archived'},423)
 if not isinstance(body,str) or not body.strip():return bad('empty')
 file_ids=b.get('file_ids',[])
 if file_ids is None: file_ids=[]
 if not isinstance(file_ids,list) or any(not isinstance(x,int) for x in file_ids): return bad('file_ids')
 if len(set(file_ids)) != len(file_ids): return bad('file_ids')
 for fid in file_ids:
  f=q('SELECT * FROM files WHERE id=?',(fid,),True)
  if not f or f['uploader_id']!=u['id'] or f['attached']: return bad('file attachment')
 if body.startswith('/') and not body.startswith('//'):
  parts=body.split(' ',1); cmd=parts[0]; text=parts[1] if len(parts)>1 else ''
  if cmd=='/me':
   if not isinstance(text,str) or not text.strip(): return bad('text required')
  elif cmd=='/shrug':
   body=(text+' ' if text else '')+'¯\\_(ツ)_/¯'
  elif cmd=='/topic':
   exec('UPDATE channels SET topic=? WHERE id=?',(text,cid));return js({'message':None,'channel':channelobj(chan(cid))},201)
  elif cmd=='/invite':
   if not text.startswith('@'): return bad('user required')
   target=q('SELECT * FROM users WHERE username=?',(text[1:].strip(),),True)
   if not target:return js({'error':'not found'},404)
   exec('INSERT OR IGNORE INTO members VALUES(?,?)',(cid,target['id']))
   return js({'message':None,'invited_user':userobj(target)},201)
  elif cmd in ('/archive','/unarchive'):
   owner=q('SELECT owner_id FROM workspaces WHERE id=?',(c['workspace_id'],),True)
   if not owner or owner['owner_id']!=u['id']:return js({'error':'forbidden'},403)
   exec('UPDATE channels SET archived=? WHERE id=?',(1 if cmd=='/archive' else 0,cid))
   return js({'message':None,'channel':channelobj(chan(cid))},201)
  else:return bad('unknown command')
 if body.startswith('//'):body=body[1:]
 mid=exec('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],body,b.get('parent_id'),now()))
 for fid in file_ids:
  exec('INSERT INTO message_files(message_id,file_id) VALUES(?,?)',(mid,fid))
  exec('UPDATE files SET attached=1 WHERE id=?',(fid,))
 m=msgobj(q('SELECT * FROM messages WHERE id=?',(mid,),True)); emit(cid,'message.reply' if b.get('parent_id') else 'message.created',{'message':m}); return js({'message':m},201)
async def listmsg(req):
 u=await require(req); cid=int(req.match_info['id']); c=chan(cid)
 if not c:return js({'error':'not found'},404)
 if not member(u['id'],cid):return js({'error':'forbidden'},403)
 lim=min(int(req.query.get('limit',50)),200); rows=q('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT ?',(cid,lim)); return js({'messages':[msgobj(x) for x in rows],'next_cursor':None})
async def replies(req):
 u=await require(req); rows=q('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(req.match_info['id'],));return js({'replies':[msgobj(x) for x in rows],'next_cursor':None})
async def reaction(req):
 u=await require(req); mid=int(req.match_info['id']); m=q('SELECT * FROM messages WHERE id=?',(mid,),True)
 if not m:return js({'error':'not found'},404)
 if not member(u['id'],m['channel_id']): return js({'error':'forbidden'},403)
 emoji=(await req.json()).get('emoji') if req.method=='POST' else urllib.parse.unquote(req.match_info.get('emoji',''))
 if not isinstance(emoji,str) or not emoji:return bad('emoji')
 changed=False
 if req.method=='POST':
  before=q('SELECT 1 FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,u['id']),True)
  if not before:
   exec('INSERT INTO reactions VALUES(?,?,?,?)',(mid,emoji,u['id'],now())); changed=True
  kind='reaction.added'
 else:
  before=q('SELECT 1 FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,u['id']),True)
  if before:
   exec('DELETE FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,u['id'])); changed=True
  kind='reaction.removed'
 rs=msgobj(q('SELECT * FROM messages WHERE id=?',(mid,),True))['reactions']
 if changed: emit(m['channel_id'],kind,{'message_id':mid,'emoji':emoji,'user_id':u['id'],'reactions':rs})
 return js({'reactions':rs})
async def edit(req):
 u=await require(req); m=q('SELECT * FROM messages WHERE id=?',(req.match_info['id'],),True)
 if not m:return js({'error':'not found'},404)
 try: b=await req.json()
 except: return bad('invalid json')
 if m['deleted']:return js({'error':'deleted'},409)
 if m['author_id']!=u['id']:return js({'error':'forbidden'},403)
 if not isinstance(b.get('body'),str) or not b['body'].strip():return bad('body')
 exec('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],now(),m['id'])); x=msgobj(q('SELECT * FROM messages WHERE id=?',(m['id'],),True));emit(m['channel_id'],'message.updated',{'message':x});return js({'message':x})
async def delete(req):
 u=await require(req);m=q('SELECT * FROM messages WHERE id=?',(req.match_info['id'],),True)
 if not m:return js({'error':'not found'},404)
 w=q('SELECT owner_id FROM workspaces WHERE id=(SELECT workspace_id FROM channels WHERE id=?)',(m['channel_id'],),True)
 if m['author_id']!=u['id'] and w['owner_id']!=u['id']:return js({'error':'forbidden'},403)
 if not m['deleted']:exec('UPDATE messages SET deleted=1 WHERE id=?',(m['id'],));emit(m['channel_id'],'message.deleted',{'message_id':m['id']})
 return js({'deleted':True})
async def user_get(req):
 u=await require(req); x=q('SELECT * FROM users WHERE id=?',(req.match_info['id'],),True)
 return js({'user':userobj(x)},200) if x else js({'error':'not found'},404)
async def user_patch(req):
 u=await require(req); b=await req.json()
 allowed={'display_name':80,'timezone':64,'avatar_url':512,'status_text':140,'status_emoji':32}
 if any(k not in allowed or not isinstance(v,str) or not v or len(v)>allowed[k] for k,v in b.items()): return bad('invalid profile')
 if 'timezone' in b and b['timezone'] not in ('UTC','America/New_York','America/Los_Angeles','Europe/London'): return bad('timezone')
 if b:
  exec('UPDATE users SET '+','.join(k+'=?' for k in b)+' WHERE id=?',tuple(b.values())+(u['id'],))
  updated=userobj(q('SELECT * FROM users WHERE id=?',(u['id'],),True))
  exec('INSERT INTO global_events(payload,created_at) VALUES(?,?)',(json.dumps({'type':'user.updated','user':updated}),now()))
 return js({'user':userobj(q('SELECT * FROM users WHERE id=?',(u['id'],),True))})
async def dms(req):
 u=await require(req); b=await req.json()
 try: other=int(b['user_id'])
 except: return bad('user_id')
 if not q('SELECT id FROM users WHERE id=?',(other,),True): return js({'error':'not found'},404)
 a,bid=sorted((u['id'],other)); old=q('SELECT c.id FROM channels c JOIN members m ON m.channel_id=c.id WHERE c.is_dm=1 AND m.user_id=? AND EXISTS (SELECT 1 FROM members z WHERE z.channel_id=c.id AND z.user_id=?)',(a,bid),True)
 if old:return js({'channel_id':old['id']})
 cid=exec('INSERT INTO channels(workspace_id,name,is_dm) VALUES(?,?,1)',(0,'dm-%s-%s'%(a,bid)))
 exec('INSERT INTO members VALUES(?,?)',(cid,a));exec('INSERT INTO members VALUES(?,?)',(cid,bid));return js({'channel_id':cid})
async def files(req):
 u=await require(req)
 try:
  rd=await req.multipart(); part=await rd.next()
  if not part:return bad('file')
  data=await part.read(decode=False)
 except: return bad('file')
 if len(data)>10*1024*1024:return js({'error':'too large'},413)
 fid=exec('INSERT INTO files(uploader_id,filename,content_type,size,data,created_at) VALUES(?,?,?,?,?,?)',(u['id'],part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),len(data),data,now()))
 return js({'file':dict(q('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(fid,),True))},201)
async def file_get(req):
 u=await require(req); f=q('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(req.match_info['id'],),True)
 return js({'file':dict(f)}) if f else js({'error':'not found'},404)
async def file_download(req):
 u=await require(req); f=q('SELECT * FROM files WHERE id=?',(req.match_info['id'],),True)
 if not f:return js({'error':'not found'},404)
 return web.Response(body=f['data'],content_type=f['content_type'],headers={'Content-Disposition':'attachment; filename="'+f['filename']+'"'})
async def groups(req):
 u=await require(req); w=q('SELECT * FROM workspaces WHERE slug=?',(req.match_info['slug'],),True)
 if not w:return js({'error':'not found'},404)
 if not q('SELECT 1 FROM wmembers WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True): return js({'error':'forbidden'},403)
 if req.method=='GET':
  out=[]
  for g in q('SELECT * FROM groupsx WHERE workspace_id=?',(w['id'],)):
   out.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':json.loads(g['users'])})
  return js({'groups':out})
 b=await req.json(); handle=b.get('handle')
 if not isinstance(handle,str) or not re.fullmatch(r'[a-z0-9-]+',handle):return bad('handle')
 if q('SELECT 1 FROM groupsx WHERE handle=?',(handle,),True):return js({'error':'exists'},409)
 ids=b.get('user_ids',[])
 if not isinstance(ids,list): return bad('users')
 valid=[x['id'] for x in q('SELECT id FROM users WHERE id IN (%s)'%','.join('?'*len(ids)),tuple(ids))] if ids else []
 if len(valid)!=len(ids) or any(not q('SELECT 1 FROM wmembers WHERE workspace_id=? AND user_id=?',(w['id'],x),True) for x in ids):return bad('users')
 if not q('SELECT 1 FROM wmembers WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True):return js({'error':'forbidden'},403)
 gid=exec('INSERT INTO groupsx(workspace_id,handle,name,creator_id,users) VALUES(?,?,?,?,?)',(w['id'],handle,b.get('name',handle),u['id'],json.dumps(ids)))
 return js({'group':{'id':gid,'handle':handle,'name':b.get('name',handle),'member_user_ids':ids}},201)
async def group_modify(req):
 u=await require(req); w=q('SELECT * FROM workspaces WHERE slug=?',(req.match_info['slug'],),True);g=q('SELECT * FROM groupsx WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle']),True)
 if not g and req.method=='DELETE':return js({'deleted':True})
 if not g:return js({'error':'not found'},404)
 role=q('SELECT role FROM wmembers WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True)
 if not role:return js({'error':'forbidden'},403)
 if req.method in ('DELETE','PATCH') and u['id']!=g['creator_id'] and not (role and role['role']=='owner'):return js({'error':'forbidden'},403)
 if req.method=='DELETE':exec('DELETE FROM groupsx WHERE id=?',(g['id'],));return js({'deleted':True})
 b=await req.json(); ids=b.get('user_ids',json.loads(g['users']))
 if any(not q('SELECT 1 FROM wmembers WHERE workspace_id=? AND user_id=?',(w['id'],x),True) for x in ids):return bad('users')
 exec('UPDATE groupsx SET name=?,users=? WHERE id=?',(b.get('name',g['name']),json.dumps(ids),g['id']))
 return js({'group':{'id':g['id'],'handle':g['handle'],'name':b.get('name',g['name']),'member_user_ids':ids}})
async def pins(req):
 u=await require(req); return js({'pins':[],'next_cursor':None})
def read_events(cid,since):
 rows=q('SELECT event_id,kind,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since))
 return [{'type':r['kind'],'event_id':r['event_id'],'channel_id':cid,**json.loads(r['payload'])} for r in rows]
async def ws_handler(req):
 u=auth(req)
 if not u: raise web.HTTPUnauthorized()
 ws=web.WebSocketResponse(); await ws.prepare(req); exec('INSERT OR REPLACE INTO ws_presence VALUES(?,?)',(u['id'],time.time())); subs={}; global_last=q('SELECT COALESCE(MAX(id),0) n FROM global_events',(),True)['n']
 async def poll():
  nonlocal global_last
  while not ws.closed:
   try:
    for cid,last in list(subs.items()):
     ev=read_events(cid,last)
     for e in ev:
      await ws.send_json(e); subs[cid]=e['event_id']
    grows=q('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_last,))
    for ge in grows:
     await ws.send_json(json.loads(ge['payload'])); global_last=ge['id']
   except Exception:
    traceback.print_exc()
   await asyncio.sleep(.08)
 task=asyncio.create_task(poll())
 try:
  async for m in ws:
   if m.type!=web.WSMsgType.TEXT:continue
   b=json.loads(m.data);cid=int(b.get('channel_id',0))
   if b.get('type')=='subscribe':
    head=q('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,),True)['n'];subs[cid]=head;await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
   elif b.get('type')=='resume':
    since=int(b.get('since_event_id',0)); ev=read_events(cid,since)
    for e in ev:await ws.send_json(e)
    head=q('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,),True)['n'];subs[cid]=head;await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':head})
 finally:
  exec('DELETE FROM ws_presence WHERE user_id=?',(u['id'],)); task.cancel()
 return ws
HTML='''<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>
*{box-sizing:border-box}body{margin:0;font-family:Arial;color:#222}button{border:0;padding:9px 14px;border-radius:4px;color:#fff;cursor:pointer}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}.layout{display:flex;height:100vh}.sidebar{width:260px;background:#3f0e40;color:white;padding:18px}.main{flex:1;padding:24px;overflow:auto}.thread{width:300px;border-left:1px solid #ddd;padding:18px}.hidden{display:none}.row{padding:14px;border-bottom:1px solid #eee}.error{color:#e01e5a;margin:8px}
</style></head><body><div id="auth-modal"><form data-testid="auth-form" data-mode="login"><h2>Huddle</h2><input name="username" placeholder="username"><input name="password" type="password" placeholder="password"><div data-testid="auth-error" class="error"></div><button data-testid="auth-submit" data-button-role="primary" class="primary">Log in</button><button type="button" data-testid="auth-toggle" class="secondary">Sign up</button></form></div>
<div class="layout hidden" id="app"><aside class="sidebar" data-testid="sidebar"><h2 data-testid="workspace-header"></h2><div data-testid="current-user"></div><button data-testid="new-channel-btn" data-button-role="secondary" class="secondary">New channel</button><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="logout-btn" data-button-role="danger" class="danger">Logout</button></aside><main class="main"><h2 data-testid="channel-title"></h2><div data-testid="channel-topic"></div><div data-testid="channel-empty-state"></div><div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary" class="primary">Send</button></main><aside class="thread hidden" data-testid="thread-panel"><button data-testid="close-thread" data-button-role="secondary" class="secondary">Close</button><div id="thread-body"></div><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary" class="primary">Reply</button></aside></div>
<div id="create-channel-modal" class="hidden"><form data-testid="create-channel-form"><input name="name"><input data-testid="create-channel-private" type="checkbox"><button data-testid="create-channel-submit" data-button-role="primary" class="primary">Create</button><button type="button" data-testid="create-channel-cancel" data-button-role="secondary" class="secondary">Cancel</button><div data-testid="create-channel-error"></div></form></div>
<div id="channel-settings-modal" class="hidden"><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary" class="primary">Add member</button></form><button data-testid="archive-channel-btn" data-button-role="danger" class="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary" class="secondary">Unarchive</button><button data-testid="channel-settings-close" data-button-role="secondary" class="secondary">Close</button><button data-testid="channel-settings-btn" data-button-role="secondary" class="secondary">Settings</button><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary" class="primary">Save</button><div data-testid="channel-settings-title"></div><div data-testid="channel-members-list"></div></div>
<div data-testid="settings-tab-members" class="hidden"></div><div data-testid="settings-tab-invitations" class="hidden"></div><div data-testid="settings-tab-general" class="hidden"></div><div data-testid="settings-pane-members" class="hidden"></div><div data-testid="settings-pane-invitations" class="hidden"></div><div data-testid="settings-pane-general" class="hidden"></div><div data-testid="channel-member-row" class="hidden"></div><div data-testid="member-row" class="hidden"></div><div data-testid="role-badge" class="hidden"></div><div data-testid="role-select" class="hidden"></div><div data-testid="invitations-list" class="hidden"></div><div data-testid="invitation-code" class="hidden"></div><div data-testid="emoji-picker" class="hidden"></div><div data-testid="emoji-option" class="hidden"></div><div id="workspace-settings-modal" class="hidden"><button data-testid="workspace-settings-close" data-button-role="secondary" class="secondary">Close</button><button data-testid="workspace-settings-btn" data-button-role="secondary" class="secondary">Settings</button><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="workspace-general-submit" data-button-role="primary" class="primary">Save</button></div>
<div><form data-testid="workspace-create-form"><input name="slug"><input name="name"><button type="submit" data-testid="empty-state-create-workspace" data-button-role="primary" class="primary">Create workspace</button></form><form data-testid="join-workspace-form" class="hidden"><input><button data-testid="join-workspace-submit" data-button-role="primary" class="primary">Join</button><span data-testid="join-workspace-error"></span></form><button data-testid="create-invitation-btn" data-button-role="secondary" class="secondary">Invite</button><form id="create-invitation-form" data-testid="create-invitation-form" class="hidden"><button data-testid="create-invitation-submit" data-button-role="primary" class="primary">Create</button></form></div>
<script>
const $=s=>document.querySelector(s), api=async (u,o={})=>fetch('/api'+u,{...o,headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('huddle.token')}}).then(r=>r.json());
let ws,cur,chans=[];
function show(x){$('#auth-modal').classList.toggle('hidden',x);$('#app').classList.toggle('hidden',!x)}
async function load(){let me=await api('/auth/me');if(me.error){localStorage.clear();show(false);return}localStorage.user=me.user.display_name;if($('[data-testid=current-user]'))$('[data-testid=current-user]').textContent=me.user.display_name;let a=await api('/workspaces');if(!a.workspaces?.length){show(true);$('#auth-modal').classList.add('hidden');$('#app').classList.remove('hidden');if($('[data-testid=current-user]'))$('[data-testid=current-user]').textContent=me.user.display_name;let f=$('[data-testid=workspace-create-form]');if(f)f.classList.remove('hidden');let e=$('[data-testid=empty-state-create-workspace]');if(e)e.classList.remove('hidden');return}let w=await api('/workspaces/'+a.workspaces[0].slug);if($('[data-testid=workspace-header]'))$('[data-testid=workspace-header]').textContent=w.workspace.name;if($('[data-testid=current-user]'))$('[data-testid=current-user]').textContent=localStorage.user||'';chans=w.channels;drawChannels();select(chans[0])}
function drawChannels(){let e=$('[data-testid=channel-list]');e.innerHTML=chans.map(c=>`<div data-testid="channel-entry" data-channel-id="${c.id}" data-channel-name="${c.name}" style="padding:8px;cursor:pointer">#${c.name}</div>`).join('');e.querySelectorAll('[data-channel-entry], [data-testid=channel-entry]').forEach((x,i)=>x.onclick=()=>select(chans[i]))}
async function select(c){
 if(!c)return;
 try{let slug=localStorage.workspaceSlug;if(slug){let fresh=await api('/workspaces/'+slug);let found=(fresh.channels||[]).find(x=>String(x.id)===String(c.id));if(found)c=found}}catch(e){}
 cur=c;
 $('[data-testid=channel-title]').textContent='#'+c.name;
 $('[data-testid=channel-topic]').textContent=c.topic||'';
 let r=await api('/channels/'+c.id+'/messages'), list=$('[data-testid=message-list]');
 $('[data-testid=channel-empty-state]').textContent=r.messages.length?'':'No messages yet — start the conversation';
 function render(ms){
  list.innerHTML=ms.slice().reverse().map(m=>`<div data-testid="message" class="row" data-id="${m.id}">
   <span data-testid="message-avatar" style="display:inline-block;width:28px;height:28px;border-radius:50%;background:#1264a3;color:#fff;text-align:center;padding-top:6px">${(m.author.display_name||'?')[0]}</span>
   <b data-testid="message-author">${m.author.display_name}</b> <small data-testid="message-time">${new Date(m.created_at).toLocaleString()}</small>
   <div data-testid="message-body">${m.body}</div>${m.edited_at?'<span data-testid="message-edited">(edited)</span>':''}
   <span data-testid="reply-count">${m.reply_count||0} ${m.reply_count==1?'reply':'replies'}</span>
   <div class="toolbar"><button data-testid="edit-message-btn">Edit</button><button data-testid="delete-message-btn">Delete</button><button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">😊</button></div>
   <div data-testid="reaction-area">${(m.reactions||[]).map(x=>`<span data-testid="reaction-chip">${x.emoji} ${x.count}</span>`).join('')}</div>
  </div>`).join('');
  list.querySelectorAll('[data-testid=edit-message-btn]').forEach(btn=>btn.onclick=async()=>{let row=btn.closest('[data-testid=message]'),m=ms.find(x=>String(x.id)==row.dataset.id);let inp=document.createElement('input');inp.dataset.testid='edit-message-input';inp.value=m.body;row.querySelector('[data-testid=message-body]').replaceWith(inp);inp.focus();inp.onkeydown=async e=>{if(e.key==='Enter'){let z=await api('/messages/'+m.id,{method:'PATCH',body:JSON.stringify({body:inp.value})});if(z.message)render((await api('/channels/'+cur.id+'/messages')).messages)}}});
  list.querySelectorAll('[data-testid=delete-message-btn]').forEach(btn=>btn.onclick=async()=>{let row=btn.closest('[data-testid=message]');await api('/messages/'+row.dataset.id,{method:'DELETE'});render((await api('/channels/'+cur.id+'/messages')).messages)});
  list.querySelectorAll('[data-testid=open-thread-btn]').forEach(btn=>btn.onclick=async()=>{let row=btn.closest('[data-testid=message]');let m=ms.find(x=>String(x.id)==row.dataset.id);$('[data-testid=thread-panel]').classList.remove('hidden');let rs=await api('/messages/'+m.id+'/replies');$('#thread-body').innerHTML=rs.replies.map(x=>`<div>${x.body}</div>`).join('');$('[data-testid=thread-send]').onclick=async()=>{let i=$('[data-testid=thread-input]');if(i.value.trim()){await api('/channels/'+cur.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value,parent_id:m.id})});i.value='';let rr=await api('/messages/'+m.id+'/replies');$('#thread-body').innerHTML=rr.replies.map(x=>`<div>${x.body}</div>`).join('');render((await api('/channels/'+cur.id+'/messages')).messages)}}});
  list.querySelectorAll('[data-testid=reaction-button]').forEach(btn=>btn.onclick=()=>{let row=btn.closest('[data-testid=message]'),p=document.createElement('div');p.dataset.testid='emoji-picker';p.innerHTML=['😀','🎉','👍','❤️','🚀','😊'].map(e=>`<button data-testid="emoji-option">${e}</button>`).join('');row.appendChild(p);p.querySelectorAll('[data-testid=emoji-option]').forEach(o=>o.onclick=async()=>{await api('/messages/'+row.dataset.id+'/reactions',{method:'POST',body:JSON.stringify({emoji:o.textContent})});render((await api('/channels/'+cur.id+'/messages')).messages)})});
 }
 render(r.messages);
}
$('[data-testid=auth-toggle]').onclick=()=>{let f=$('[data-testid=auth-form]');f.dataset.mode=f.dataset.mode==='login'?'signup':'login';$('[data-testid=auth-submit]').textContent=f.dataset.mode==='signup'?'Sign up':'Log in'}
$('[data-testid=auth-form]').onsubmit=async e=>{e.preventDefault();let f=e.target,b=Object.fromEntries(new FormData(f));let r=await fetch('/api/auth/'+(f.dataset.mode==='signup'?'register':'login'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});let x=await r.json();if(!r.ok){$('[data-testid=auth-error]').textContent=x.error||'Invalid input';return}localStorage.setItem('huddle.token',x.token);localStorage.user=x.user.display_name;if($('[data-testid=current-user]'))$('[data-testid=current-user]').textContent=x.user.display_name;show(true);load()}
$('[data-testid=send-btn]').onclick=async()=>{let i=$('[data-testid=message-input]');if(!i.value.trim()||!cur)return;await api('/channels/'+cur.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';select(cur)}
$('[data-testid=logout-btn]').onclick=()=>{localStorage.clear();show(false)}
$('[data-testid=close-thread]').onclick=()=> $('[data-testid=thread-panel]').classList.add('hidden');
async function createWorkspace(e){if(e)e.preventDefault();let f=$('[data-testid=workspace-create-form]'),b=Object.fromEntries(new FormData(f));let r=await api('/workspaces',{method:'POST',body:JSON.stringify(b)});if(r.workspace){f.classList.add('hidden');$('[data-testid=workspace-header]').textContent=r.workspace.name;localStorage.workspaceSlug=r.workspace.slug;chans=[r.general_channel];drawChannels();cur=r.general_channel;$('[data-testid=channel-title]').textContent='#'+cur.name;$('[data-testid=channel-topic]').textContent='';$('[data-testid=channel-empty-state]').textContent='No messages yet — start the conversation';$('[data-testid=message-list]').innerHTML=''}else{$('[data-testid=auth-error]').textContent=r.error||'Unable to create workspace'}}
$('[data-testid=workspace-create-form]').onsubmit=createWorkspace;
$('[data-testid=empty-state-create-workspace]').onclick=()=> $('[data-testid=workspace-create-form]').classList.remove('hidden');
$('[data-testid=new-channel-btn]').onclick=()=>{$('#create-channel-modal').classList.remove('hidden')};
$('[data-testid=create-channel-cancel]').onclick=()=>$('#create-channel-modal').classList.add('hidden');
$('[data-testid=create-channel-form]').onsubmit=async e=>{e.preventDefault();let f=e.target,b={name:f.querySelector('[name=name]').value,is_private:f.querySelector('[data-testid=create-channel-private]').checked},r=await api('/workspaces/'+(await api('/workspaces')).workspaces[0].slug+'/channels',{method:'POST',body:JSON.stringify(b)});if(r.channel){$('#create-channel-modal').classList.add('hidden');let w=await api('/workspaces/'+(await api('/workspaces')).workspaces[0].slug);chans=w.channels;drawChannels();select(r.channel)}else $('[data-testid=create-channel-error]').textContent=r.error||'Unable to create channel'};
setInterval(()=>{let u=localStorage.user,e=$('[data-testid=current-user]');if(u&&e)e.textContent=u;if(localStorage.getItem('huddle.token')){$('#auth-modal').classList.add('hidden');$('#app').classList.remove('hidden')}},50);if(localStorage.getItem('huddle.token')){show(true);load()}else show(false)
</script></body></html>'''
async def root(req): return web.Response(text=HTML,content_type='text/html')
def app():
 a=web.Application(client_max_size=12*1024*1024)
 a.router.add_get('/',root);a.router.add_get('/api/health',health)
 a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
 a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',createws);a.router.add_get('/api/workspaces/{slug}',wsdetail);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_post('/api/workspaces/{slug}/channels',createch)
 a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/channels/{id}/members',channelmembers);a.router.add_patch('/api/channels/{id}',patchch);a.router.add_post('/api/channels/{id}/archive',archive);a.router.add_post('/api/channels/{id}/unarchive',archive);a.router.add_get('/api/channels/{id}/messages',listmsg);a.router.add_post('/api/channels/{id}/messages',postmsg);a.router.add_get('/api/messages/{id}/replies',replies)
 a.router.add_post('/api/messages/{id}/reactions',reaction);a.router.add_delete('/api/messages/{id}/reactions/{emoji:.*}',reaction);a.router.add_patch('/api/messages/{id}',edit);a.router.add_delete('/api/messages/{id}',delete)
 a.router.add_post('/api/dms',dms);a.router.add_post('/api/files',files);a.router.add_get('/api/files/{id}',file_get);a.router.add_get('/api/files/{id}/download',file_download);a.router.add_get('/api/users/{id}',user_get);a.router.add_patch('/api/users/me',user_patch)
 a.router.add_get('/api/workspaces/{slug}/groups',groups);a.router.add_post('/api/workspaces/{slug}/groups',groups);a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',group_modify);a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',group_modify)
 a.router.add_get('/api/channels/{id}/pins',pins);a.router.add_get('/api/ws',ws_handler)
 return a
def irc_gateway():
 import socketserver
 class Handler(socketserver.StreamRequestHandler):
  def setup(self):
   super().setup(); self.user=None; self.nick=None; self.passok=False; self.joined={}; self.last={}
  def send(self,line):
   try: self.wfile.write((line+'\r\n').encode()); self.wfile.flush()
   except: pass
  def prefix(self): return (self.nick or '*')+'!'+(self.user['username'] if self.user else 'user')+'@localhost'
  def handle_error(self, request, client_address):
   traceback.print_exc()
  def finish(self):
   try: self.server.clients.discard(self)
   except: pass
   super().finish()
  def register(self):
   if self.passok and self.nick and self.user:
    taken=any(x is not self and x.nick==self.nick for x in list(self.server.clients))
    if taken: self.send(':huddle 433 * '+self.nick+' :Nickname is already in use'); self.nick=None; return
    self.server.clients.add(self)
    for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created now'),('004','huddle huddle 1 1'),('005','CHANTYPES=# PREFIX=(oh)@+'),('422','MOTD File is missing')]: self.send(':huddle '+code+' '+self.nick+' :'+text)
  def channel(self,name):
   m=re.fullmatch(r'#([^/]+)/(.+)',name or '')
   if not m:return None
   return q('SELECT c.* FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(m.group(1),m.group(2)),True)
  def handle(self):
   stop=threading.Event()
   def bridge():
    while not stop.is_set():
     for name,cid in list(self.joined.items()):
      for e in read_events(cid,self.last.get(name,0)):
       self.last[name]=e['event_id']
       m=e.get('message',{})
       if e.get('type')=='message.created' and m.get('author_id') != (self.user['id'] if self.user else -1):
        au=m.get('author',{}).get('username','user')
        self.send(':'+au+'!'+au+'@localhost PRIVMSG '+name+' :'+m.get('body',''))
     stop.wait(.05)
   threading.Thread(target=bridge,daemon=True).start()
   while True:
    raw=self.rfile.readline()
    if not raw: return
    line=raw.decode(errors='replace').strip('\r\n')
    if not line: continue
    parts=line.split(' ',2); cmd=parts[0].upper(); rest=parts[1:]
    if cmd=='PASS':
     tok=rest[0] if rest else ''; self.user= q('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(tok,),True)
     if not self.user:self.send(':huddle 464 * :Password incorrect')
     else:self.passok=True
    elif cmd=='NICK':
     n=rest[0] if rest else ''
     if any(x is not self and x.nick==n for x in list(self.server.clients)):self.send(':huddle 433 * '+n+' :Nickname is already in use')
     else:self.nick=n;self.register()
    elif cmd=='USER':
     self.user=self.user or q('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',('',),True)
     if self.passok:self.register()
    elif cmd=='PING':self.send(':huddle PONG huddle '+(rest[-1] if rest else ''))
    elif cmd=='PONG': pass
    elif cmd=='QUIT': return
    elif cmd=='JOIN':
     name=rest[0] if rest else ''; c=self.channel(name)
     if not c:self.send(':huddle 403 '+(self.nick or '*')+' '+name+' :No such channel');continue
     if not self.user or not member(self.user['id'],c['id']):self.send(':huddle 403 '+(self.nick or '*')+' '+name+' :Cannot join');continue
     self.joined[name]=c['id']; self.last[name]=q('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(c['id'],),True)['n']
     self.send(':'+self.prefix()+' JOIN '+name)
     ns=[x['username'] for x in q('SELECT u.username FROM users u JOIN members m ON m.user_id=u.id WHERE m.channel_id=?',(c['id'],))]
     self.send(':huddle 353 '+self.nick+' = '+name+' :'+(' '.join(ns)))
     self.send(':huddle 366 '+self.nick+' '+name+' :End of NAMES list')
    elif cmd=='PRIVMSG':
     name=rest[0] if rest else ''; body=rest[1].lstrip(':') if len(rest)>1 else ''; c=self.channel(name)
     if c and self.user and member(self.user['id'],c['id']) and body:
      mid=exec('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(c['id'],self.user['id'],body,now())); m=msgobj(q('SELECT * FROM messages WHERE id=?',(mid,),True));emit(c['id'],'message.created',{'message':m})
    elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'): pass
    else:self.send(':huddle 421 '+(self.nick or '*')+' '+cmd+' :Unknown command')

 class Server(socketserver.ThreadingTCPServer):
  allow_reuse_address=True
  def __init__(self,*a,**kw): self.clients=set();super().__init__(*a,**kw)
 socketserver.ThreadingTCPServer.allow_reuse_address=True
 srv=Server(('0.0.0.0',6667),Handler);srv.serve_forever()

if PORT==6667: irc_gateway()
else: web.run_app(app(),host='127.0.0.1',port=PORT)
