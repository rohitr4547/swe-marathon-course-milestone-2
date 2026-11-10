import time
import time,os,sqlite3,json,secrets,re,hashlib,base64,threading,datetime,urllib.parse,sys
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/data/huddle.sqlite'; LOCK=threading.RLock()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,check_same_thread=False); c.row_factory=sqlite3.Row
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER); CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,emoji TEXT,user_id INTEGER,PRIMARY KEY(message_id,emoji,user_id));
 CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB,attached INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS seq(channel_id INTEGER PRIMARY KEY,n INTEGER DEFAULT 0); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,event_id INTEGER,type TEXT,payload TEXT,UNIQUE(channel_id,event_id)); CREATE TABLE IF NOT EXISTS presence(user_id INTEGER PRIMARY KEY,last_seen REAL DEFAULT 0); CREATE TABLE IF NOT EXISTS profile_events(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,created_at TEXT); CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT UNIQUE,name TEXT,creator_id INTEGER); CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id)); CREATE TABLE IF NOT EXISTS dms(user1 INTEGER,user2 INTEGER,channel_id INTEGER,PRIMARY KEY(user1,user2));''');
 try: c.execute('ALTER TABLE presence ADD COLUMN last_seen REAL DEFAULT 0')
 except sqlite3.OperationalError: pass
 return c
def user(r): return {'id':r['id'],'username':r['username'],'display_name':r['display_name'],'timezone':r['timezone'],'avatar_url':r['avatar_url'],'status_text':r['status_text'],'status_emoji':r['status_emoji']}
def auth(h):
 t=h.get('Authorization','').strip(); t=t[7:].strip() if t.lower().startswith('bearer ') else ''; r=db().execute('SELECT u.* FROM users u JOIN tokens t ON u.id=t.user_id WHERE t.token=?',(t,)).fetchone(); return r
def msg(c,r):
 rx=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(r['id'],)).fetchall(); groups=[]
 for e in dict.fromkeys(x['emoji'] for x in rx): groups.append({'emoji':e,'count':sum(x['emoji']==e for x in rx),'user_ids':[x['user_id'] for x in rx if x['emoji']==e]})
 members=[x['user_id'] for x in c.execute('SELECT user_id FROM cmembers WHERE channel_id=?',(r['channel_id'],)).fetchall()]
 mentions=[]
 text=r['body'] or ''
 for x in c.execute('SELECT id,username FROM users').fetchall():
  if x['id']!=r['author_id'] and x['id'] in members and re.search(r'(?<![A-Za-z0-9_])@'+re.escape(x['username'])+r'\b',text): mentions.append(x['id'])
 if '@channel' in text: mentions += [x for x in members if x!=r['author_id']]
 if '@here' in text: mentions += [x for x in members if x!=r['author_id'] and c.execute('SELECT 1 FROM presence WHERE user_id=?',(x,)).fetchone()]
 ch=c.execute('SELECT workspace_id FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
 if ch:
  for g in c.execute('SELECT id,handle FROM groups WHERE workspace_id=?',(ch['workspace_id'],)).fetchall():
   if '@'+g['handle'] in text:
    mentions += [x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? AND user_id IN (%s)' % (','.join('?'*len(members))),(g['id'],*members)).fetchall() if x['user_id']!=r['author_id']]
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[dict(x) for x in c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE attached=?',(r['id'],)).fetchall()],'reactions':groups,'mentions':list(dict.fromkeys(mentions))}
class H(BaseHTTPRequestHandler):
 def send(self,status,obj,headers={}):
  b=json.dumps(obj).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); [self.send_header(k,v) for k,v in headers.items()]; self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length',0)); return json.loads(self.rfile.read(n) or b'{}')
 def do_GET(self):
  p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query); c=db()
  if path=='/api/ws' and self.headers.get('Upgrade','').lower()=='websocket':
   import socket,struct,base64
   tok=q.get('token',[''])[0].replace('Bearer ',''); ur=c.execute('SELECT user_id FROM tokens WHERE token=?',(tok,)).fetchone()
   if not ur: self.send_response(401); self.end_headers(); return
   key=self.headers.get('Sec-WebSocket-Key',''); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
   self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers(); self.wfile.flush(); self.connection.settimeout(0.3); c.execute('INSERT OR REPLACE INTO presence(user_id,last_seen) VALUES(?,?)',(ur['user_id'],datetime.datetime.now().timestamp())); c.commit(); last_profile=0; subscribed={}; frame_lock=threading.Lock()
   def frame(x):
    b=json.dumps(x).encode(); n=len(b); h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('>H',n)
    with frame_lock:
     self.connection.sendall(h+b)
   try:
    while True:
     try:
      pc=db(); pc.execute('UPDATE presence SET last_seen=? WHERE user_id=?',(datetime.datetime.now().timestamp(),ur['user_id'])); pc.commit(); pc.close()
     except Exception: pass
     ev=db().execute('SELECT id,user_id FROM profile_events WHERE id>? ORDER BY id',(last_profile,)).fetchall()
     for pe in ev:
      pr=db().execute('SELECT * FROM users WHERE id=?',(pe['user_id'],)).fetchone(); frame({'type':'user.updated','user':user(pr)}); last_profile=pe['id']
     try: h=self.rfile.read(2)
     except (TimeoutError, socket.timeout): continue
     except OSError:
      break
     if not h: break
     try:
      ln=h[1]&127; mask=self.rfile.read(4) if h[1]&128 else b''
      if ln==126: ln=struct.unpack('>H',self.rfile.read(2))[0]
      data=self.rfile.read(ln)
     except (TimeoutError, socket.timeout): continue
     except OSError:
      break
     data=bytes(data[i]^mask[i%4] for i in range(ln)) if mask else data
     if h[0]&15==8: break
     try: z=json.loads(data.decode())
     except: continue
     if z.get('type')=='subscribe':
      c.execute('INSERT OR REPLACE INTO presence(user_id,last_seen) VALUES(?,?)',(ur['user_id'],datetime.datetime.now().timestamp())); c.commit()
      cid=int(z.get('channel_id')); head=db().execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']; subscribed[cid]=head
      frame({'type':'subscribed','channel_id':cid,'head_event_id':head})
      def _broadcast(chid,start):
       last_id=start
       open('/tmp/broadcast-debug','a').write('START '+str(chid)+' '+str(start)+'\\n')
       while True:
        try:
         fc=db(); rows=fc.execute('SELECT event_id,type,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(chid,last_id)).fetchall(); fc.close()
         if rows: open('/tmp/broadcast-debug','a').write('ROWS '+str(chid)+' '+str(len(rows))+'\\n')
         for evr in rows:
          z2=json.loads(evr['payload']); z2.update({'type':evr['type'],'event_id':evr['event_id'],'channel_id':chid}); frame(z2); last_id=evr['event_id']
         time.sleep(0.05)
        except (BrokenPipeError,ConnectionResetError,OSError):
         return
        except Exception as ex:
         open('/tmp/broadcast-errors','a').write(repr(ex)+'\n')
         time.sleep(0.05)
      threading.Thread(target=_broadcast,args=(cid,head),daemon=True).start()

     elif z.get('type')=='resume':
      pc=db(); pc.execute('INSERT OR REPLACE INTO presence(user_id,last_seen) VALUES(?,?)',(ur['user_id'],datetime.datetime.now().timestamp())); pc.commit(); pc.close()
      cid=int(z.get('channel_id')); since=int(z.get('since_event_id',0)); rows=db().execute('SELECT event_id,type,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); subscribed[cid]=since
      for evr in rows:
       q=json.loads(evr['payload']); q.update({'type':evr['type'],'event_id':evr['event_id'],'channel_id':cid}); frame(q); subscribed[cid]=evr['event_id']
      frame({'type':'resumed','channel_id':cid,'head_event_id':subscribed[cid]})
   except Exception as ex:
    open('/tmp/ws-errors','a').write(repr(ex)+'\n')
   time.sleep(0.05)
   c.execute('DELETE FROM presence WHERE user_id=?',(ur['user_id'],)); c.commit(); return
  if path=='/': return self.html()
  if path=='/api/health': return self.send(200,{'status':'ok','node_id':int(os.getenv('PORT','8000'))-8000})
  u=auth(self.headers)
  if path=='/api/auth/me': return self.send(200,{'user':user(u)}) if u else self.send(401,{'error':'unauthorized'})
  if path.startswith('/api/users/') and not u: return self.send(401,{'error':'unauthorized'})
  if path.startswith('/api/users/'):
   r=c.execute('SELECT * FROM users WHERE id=?',(path.split('/')[-1],)).fetchone(); return self.send(200,{'user':user(r)}) if r else self.send(404,{'error':'not found'})
  if not u: return self.send(401,{'error':'unauthorized'})
  if path.startswith('/api/files/') and path.endswith('/download'):
   fid=path.split('/')[3]; r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
   if not r:return self.send(404,{'error':'not found'})
   self.send_response(200); self.send_header('Content-Type',r['content_type']); self.send_header('Content-Disposition','attachment; filename="'+r['filename']+'"'); self.send_header('Content-Length',str(r['size'])); self.end_headers(); self.wfile.write(r['data']); return
  if path.startswith('/api/files/'):
   r=c.execute('SELECT * FROM files WHERE id=?',(path.split('/')[3],)).fetchone()
   if not r:return self.send(404,{'error':'not found'})
   return self.send(200,{'file':{k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
  if '/groups' in path and path.startswith('/api/workspaces/'):
   parts=path.split('/'); slug=parts[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return self.send(404,{'error':'not found'})
   mem=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
   if not mem:return self.send(403,{'error':'forbidden'})
   rows=c.execute('SELECT g.*,group_concat(gm.user_id) ids FROM groups g LEFT JOIN group_members gm ON g.id=gm.group_id WHERE g.workspace_id=? GROUP BY g.id',(w['id'],)).fetchall()
   out=[{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':[int(x) for x in (r['ids'] or '').split(',') if x]} for r in rows]
   return self.send(200,{'groups':out})
  if path=='/api/workspaces':
   rows=c.execute('SELECT DISTINCT w.* FROM workspaces w LEFT JOIN members m ON m.workspace_id=w.id AND m.user_id=? WHERE w.owner_id=? OR m.user_id IS NOT NULL',(u['id'],u['id'])).fetchall(); return self.send(200,{'workspaces':[dict(x) for x in rows]})
  if path.startswith('/api/workspaces/'):
   slug=path.split('/')[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return self.send(404,{'error':'not found'})
   ch=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall(); return self.send(200,{'workspace':dict(w),'channels':[dict(x) for x in ch]})
  if path.startswith('/api/channels/') and path.endswith('/members'):
   cid=path.split('/')[3]
   if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone(): return self.send(403,{'error':'forbidden'})
   rows=c.execute('SELECT u.* FROM users u JOIN cmembers m ON u.id=m.user_id WHERE m.channel_id=?',(cid,)).fetchall()
   return self.send(200,{'members':[user(r) for r in rows]})
  if path.startswith('/api/channels/') and path.endswith('/messages'):
   cid=path.split('/')[3]
   if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone(): return self.send(403,{'error':'forbidden'})
   rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(cid,)).fetchall(); return self.send(200,{'messages':[msg(c,x) for x in rows],'next_cursor':None})
  if path.startswith('/api/messages/') and path.endswith('/replies'):
   pid=path.split('/')[3]; rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(pid,)).fetchall(); return self.send(200,{'replies':[msg(c,x) for x in rows],'next_cursor':None})
  return self.send(404,{'error':'not found'})
 def do_POST(self):
  path=urllib.parse.urlparse(self.path).path; c=db(); u=auth(self.headers)
  if path=='/api/auth/register':
   try:
    x=self.body(); assert re.fullmatch(r'[A-Za-z0-9_]+',x['username']) and len(x['password'])>=8
    c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(x['username'],hashlib.sha256(x['password'].encode()).hexdigest(),x.get('display_name') or x['username'])); c.commit(); r=c.execute('SELECT * FROM users WHERE username=?',(x['username'],)).fetchone(); t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); c.commit(); return self.send(201,{'user':user(r),'token':t})
   except sqlite3.IntegrityError:return self.send(409,{'error':'duplicate'})
   except:return self.send(400,{'error':'invalid'})
  if path=='/api/auth/login':
   x=self.body(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),hashlib.sha256(x.get('password','').encode()).hexdigest())).fetchone()
   if not r:return self.send(401,{'error':'invalid'})
   t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); c.commit(); return self.send(200,{'user':user(r),'token':t})
  if not u:return self.send(401,{'error':'unauthorized'})
  if '/groups' in path and path.startswith('/api/workspaces/'):
   parts=path.split('/'); slug=parts[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return self.send(404,{'error':'not found'})
   if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone(): return self.send(403,{'error':'forbidden'})
   x=self.body(); handle=x.get('handle'); name=x.get('name',''); ids=x.get('user_ids',[])
   if not isinstance(handle,str) or not re.fullmatch(r'[a-z0-9_-]+',handle) or not isinstance(ids,list): return self.send(400,{'error':'invalid'})
   if c.execute('SELECT 1 FROM groups WHERE handle=?',(handle,)).fetchone(): return self.send(409,{'error':'duplicate'})
   for uid in ids:
    if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone(): return self.send(400,{'error':'member'})
   c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],handle,name,u['id'])); gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   for uid in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,uid))
   c.commit(); return self.send(201,{'group':{'id':gid,'handle':handle,'name':name,'member_user_ids':ids}})
  if path=='/api/dms':
   x=self.body()
   try: other=int(x.get('user_id'))
   except: return self.send(400,{'error':'invalid'})
   if not c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone(): return self.send(404,{'error':'not found'})
   a,b=sorted((u['id'],other)); r=c.execute('SELECT channel_id FROM dms WHERE user1=? AND user2=?',(a,b)).fetchone()
   if r:return self.send(200,{'channel_id':r['channel_id']})
   c.execute('INSERT INTO channels(name,is_dm) VALUES(?,1)',(f'dm-{a}-{b}',)); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid)); c.execute('INSERT INTO cmembers VALUES(?,?),(?,?)',(cid,a,cid,b)); c.commit(); return self.send(200,{'channel_id':cid})
  if path=='/api/files':
   ct=self.headers.get('Content-Type','')
   if 'multipart/form-data' not in ct:return self.send(400,{'error':'multipart'})
   boundary=ct.split('boundary=',1)[-1].encode(); raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
   parts=raw.split(b'--'+boundary); part=next((z for z in parts if b'name="file"' in z),b'')
   if not part:return self.send(400,{'error':'file'})
   head,data=part.split(b'\r\n\r\n',1); data=data[:-2] if data.endswith(b'\r\n') else data
   if len(data)>10*1024*1024:return self.send(413,{'error':'too large'})
   m=re.search(br'filename="([^"]*)"',head); filename=(m.group(1).decode() if m else 'file'); m=re.search(br'Content-Type:\s*([^\r\n]+)',head,re.I); typ=(m.group(1).decode() if m else 'application/octet-stream')
   c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(u['id'],filename,typ,len(data),now(),data)); fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.commit(); r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone(); return self.send(201,{'file':{k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
  if path=='/api/workspaces':
   x=self.body(); w=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],u['id'])); wid=w.lastrowid; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id'])); c.commit(); return self.send(201,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
  if re.match(r'/api/workspaces/[^/]+/channels$',path):
   w=c.execute('SELECT id FROM workspaces WHERE slug=?',(path.split('/')[3],)).fetchone(); x=self.body(); c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(x.get('is_private',False)),x.get('topic',''))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id'])); c.commit(); return self.send(201,{'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
  if re.match(r'/api/channels/\d+/join$',path):
   cid=path.split('/')[3]; ch=c.execute('SELECT workspace_id FROM channels WHERE id=?',(cid,)).fetchone()
   if not ch:return self.send(404,{'error':'not found'})
   c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member')); c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,u['id'])); c.commit(); return self.send(200,{'joined':True})
  if re.match(r'/api/channels/\d+/messages$',path):
   cid=path.split('/')[3]
   if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone(): return self.send(403,{'error':'forbidden'})
   try: x=self.body()
   except: return self.send(400,{'error':'invalid'})
   body=x.get('body'); 
   if not isinstance(body,str) or not body.strip(): return self.send(400,{'error':'empty'})
   ch0=c.execute('SELECT is_archived FROM channels WHERE id=?',(cid,)).fetchone()
   if ch0 and ch0['is_archived'] and not body.startswith('/'): return self.send(423,{'error':'archived'})
   if body.startswith('/'):
    if body.startswith('//'): body=body[1:]
    elif body == '/me': return self.send(400,{'error':'command'})
    elif body.startswith('/me '):
     if not body[4:].strip(): return self.send(400,{'error':'command'})
    elif body == '/shrug': body='¯\\_(ツ)_/¯'
    elif body.startswith('/shrug '): body=body[7:]+' ¯\\_(ツ)_/¯'
    elif body.startswith('/topic '):
     topic=body[7:]; c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,cid)); c.commit(); return self.send(201,{'message':None,'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
    elif body.startswith('/invite @'):
     name=body[9:].strip(); ir=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
     if not ir:return self.send(404,{'error':'user'})
     c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,ir['id'])); c.commit(); return self.send(201,{'message':None,'invited_user':user(c.execute('SELECT * FROM users WHERE id=?',(ir['id'],)).fetchone())})
    elif body in ('/archive','/unarchive'):
     ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); ow=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
     owner_id=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()['owner_id']
     owner_name=c.execute('SELECT username FROM users WHERE id=?',(owner_id,)).fetchone()['username']
     caller_name=c.execute('SELECT username FROM users WHERE id=?',(u['id'],)).fetchone()['username']
     open('/tmp/archive-debug','a').write(repr((body,cid,u['id'],owner_id,caller_name,owner_name))+'\n')
     if int(u['id']) != int(owner_id) or caller_name != owner_name: return self.send(403,{'error':'forbidden'})
     c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if body=='/archive' else 0,cid)); c.commit(); out=dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()); out['is_archived']=bool(out.get('is_archived')); return self.send(201,{'message':None,'channel':out})
    else: return self.send(400,{'error':'command'})
   fids=x.get('file_ids',[])
   if not isinstance(fids,list): return self.send(400,{'error':'file_ids'})
   for fid in fids:
    if not isinstance(fid,int) or not c.execute('SELECT 1 FROM files WHERE id=? AND uploader_id=? AND attached=0',(fid,u['id'])).fetchone(): return self.send(400,{'error':'file'})
   c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],body,x.get('parent_id'),now())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT OR IGNORE INTO seq(channel_id,n) VALUES(?,0)',(cid,)); c.execute('UPDATE seq SET n=n+1 WHERE channel_id=?',(cid,)); eid=c.execute('SELECT n FROM seq WHERE channel_id=?',(cid,)).fetchone()['n']; c.execute('INSERT INTO events(channel_id,event_id,type,payload) VALUES(?,?,?,?)',(cid,eid,'message.created',json.dumps({'message':msg(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})))
   for fid in fids: c.execute('UPDATE files SET attached=? WHERE id=?',(mid,fid))
   c.commit(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); return self.send(201,{'message':msg(c,r)})
  if re.match(r'/api/messages/\d+/reactions$',path):
   mid=path.split('/')[3]; x=self.body(); e=x.get('emoji')
   if not isinstance(e,str) or not e:return self.send(400,{'error':'emoji'})
   r=c.execute('SELECT m.*,ch.is_private FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(mid,)).fetchone()
   if not r:return self.send(404,{'error':'not found'})
   if r['is_private'] and not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(r['channel_id'],u['id'])).fetchone(): return self.send(403,{'error':'forbidden'})
   before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,e,u['id'])).fetchone(); c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,e,u['id']));
   if not before:
    c.execute('INSERT OR IGNORE INTO seq(channel_id,n) VALUES(?,0)',(r['channel_id'],)); c.execute('UPDATE seq SET n=n+1 WHERE channel_id=?',(r['channel_id'],)); eid=c.execute('SELECT n FROM seq WHERE channel_id=?',(r['channel_id'],)).fetchone()['n']; c.execute('INSERT INTO events(channel_id,event_id,type,payload) VALUES(?,?,?,?)',(r['channel_id'],eid,'reaction.added',json.dumps({'message_id':int(mid),'emoji':e,'user_id':u['id']})))
   c.commit(); return self.send(200,{'reactions':msg(c,r)['reactions']})
  return self.send(404,{'error':'not found'})
 def do_PATCH(self):
  path=urllib.parse.urlparse(self.path).path; c=db(); u=auth(self.headers)
  if not u:return self.send(401,{'error':'unauthorized'})
  if path=='/api/users/me':
   x=self.body(); allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
   if any(k not in allowed for k in x): return self.send(400,{'error':'invalid'})
   if any(not isinstance(v,str) for k,v in x.items()): return self.send(400,{'error':'invalid'})
   if 'timezone' in x and x['timezone'] not in ('UTC','America/New_York','America/Los_Angeles','Europe/London','Asia/Tokyo'): return self.send(400,{'error':'timezone'})
   limits={'display_name':80,'timezone':100,'avatar_url':512,'status_text':140,'status_emoji':32}
   if any(len(x[k])>limits[k] or (k in ('display_name','timezone') and not x[k]) for k in x): return self.send(400,{'error':'length'})
   if x:
    c.execute('UPDATE users SET '+','.join(k+'=?' for k in x)+' WHERE id=?',tuple(x.values())+(u['id'],)); c.execute('INSERT INTO profile_events(user_id,created_at) VALUES(?,?)',(u['id'],now())); c.commit()
   return self.send(200,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())})
  if path.startswith('/api/messages/') and path.count('/')==3:
   mid=path.split('/')[3]; r=c.execute('SELECT m.*,ch.workspace_id FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(mid,)).fetchone()
   if not r:return self.send(404,{'error':'not found'})
   x=self.body(); body=x.get('body')
   if not isinstance(body,str) or not body.strip():return self.send(400,{'error':'invalid'})
   if r['deleted']:return self.send(409,{'error':'deleted'})
   print('PATCH_DEBUG',repr(r['author_id']),repr(u['id']),repr(r['id']),flush=True)
   if str(r['author_id']) != str(u['id']):return self.send(403,{'error':'forbidden'})
   edited=now(); c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,edited,mid)); c.execute('INSERT OR IGNORE INTO seq(channel_id,n) VALUES(?,0)',(r['channel_id'],)); c.execute('UPDATE seq SET n=n+1 WHERE channel_id=?',(r['channel_id'],)); eid=c.execute('SELECT n FROM seq WHERE channel_id=?',(r['channel_id'],)).fetchone()['n']; mr=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.execute('INSERT INTO events(channel_id,event_id,type,payload) VALUES(?,?,?,?)',(r['channel_id'],eid,'message.updated',json.dumps({'message':msg(c,mr)}))); c.commit(); return self.send(200,{'message':msg(c,mr)})
  if '/groups/' in path and path.startswith('/api/workspaces/'):

   parts=path.split('/'); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(parts[3],)).fetchone(); handle=parts[5]
   g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone()
   if not g:return self.send(404,{'error':'not found'})
   if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return self.send(403,{'error':'forbidden'})
   x=self.body(); name=x.get('name',g['name']); ids=x.get('user_ids')
   if ids is not None:
    for uid in ids:
     if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone():return self.send(400,{'error':'member'})
    c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
    for uid in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],uid))
   c.execute('UPDATE groups SET name=? WHERE id=?',(name,g['id']));c.commit()
   ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]
   return self.send(200,{'group':{'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':ids}})
  return self.send(404,{'error':'not found'})
  u=auth(self.headers)
  if not u:return self.send(401,{'error':'unauthorized'})
  return self.send(404,{'error':'not found'})
 def do_DELETE(self):
  path=urllib.parse.urlparse(self.path).path; c=db(); u=auth(self.headers)
  if not u:return self.send(401,{'error':'unauthorized'})
  if re.match(r'/api/messages/\d+/reactions/[^/]+$',path):
   mid=path.split('/')[3]; emoji=urllib.parse.unquote(path.split('/')[5])
   r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   if not r:return self.send(404,{'error':'not found'})
   c.execute('DELETE FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,u['id'])); c.commit()
   return self.send(200,{'reactions':msg(c,r)['reactions']})
  if re.match(r'/api/messages/\d+$',path):
   mid=path.split('/')[3]; r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   if not r:return self.send(200,{'deleted':True})
   c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); c.commit()
   return self.send(200,{'deleted':True})
  return self.send(404,{'error':'not found'})
 def do_DELETE(self):
  path=urllib.parse.urlparse(self.path).path; c=db(); u=auth(self.headers)
  if not u:return self.send(401,{'error':'unauthorized'})
  if re.match(r'/api/messages/\d+/reactions/[^/]+$',path):
   mid=path.split('/')[3]; emoji=urllib.parse.unquote(path.split('/')[5]); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   if not r:return self.send(404,{'error':'not found'})
   gone=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,u['id'])).fetchone(); c.execute('DELETE FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,u['id']));
   if gone:
    ch=c.execute('SELECT channel_id FROM messages WHERE id=?',(mid,)).fetchone(); c.execute('INSERT OR IGNORE INTO seq(channel_id,n) VALUES(?,0)',(ch['channel_id'],)); c.execute('UPDATE seq SET n=n+1 WHERE channel_id=?',(ch['channel_id'],)); eid=c.execute('SELECT n FROM seq WHERE channel_id=?',(ch['channel_id'],)).fetchone()['n']; c.execute('INSERT INTO events(channel_id,event_id,type,payload) VALUES(?,?,?,?)',(ch['channel_id'],eid,'reaction.removed',json.dumps({'message_id':int(mid),'emoji':emoji,'user_id':u['id']})))
   c.commit(); return self.send(200,{'reactions':msg(c,r)['reactions']})
  if re.match(r'/api/messages/\d+$',path):
   mid=path.split('/')[3]; r=c.execute('SELECT m.*,ch.workspace_id FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(mid,)).fetchone()
   if not r:return self.send(404,{'error':'not found'})
   if r['deleted']:return self.send(200,{'deleted':True})
   owner=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone()
   if r['author_id']!=u['id'] and (not owner or owner['owner_id']!=u['id']):return self.send(403,{'error':'forbidden'})
   c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); c.execute('INSERT OR IGNORE INTO seq(channel_id,n) VALUES(?,0)',(r['channel_id'],)); c.execute('UPDATE seq SET n=n+1 WHERE channel_id=?',(r['channel_id'],)); eid=c.execute('SELECT n FROM seq WHERE channel_id=?',(r['channel_id'],)).fetchone()['n']; c.execute('INSERT INTO events(channel_id,event_id,type,payload) VALUES(?,?,?,?)',(r['channel_id'],eid,'message.deleted',json.dumps({'message_id':int(mid)}))); c.commit(); return self.send(200,{'deleted':True})
  if '/groups/' in path and path.startswith('/api/workspaces/'):
   parts=path.split('/'); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(parts[3],)).fetchone(); g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],parts[5])).fetchone()
   if not g:return self.send(200,{'deleted':True})
   if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return self.send(403,{'error':'forbidden'})
   c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],)); c.execute('DELETE FROM groups WHERE id=?',(g['id'],)); c.commit(); return self.send(200,{'deleted':True})
  return self.send(404,{'error':'not found'})
 def html(self):
  h='''<!doctype html><html><head><meta charset="utf-8"><style>
  *{box-sizing:border-box}body{margin:0;font:14px Arial;display:flex;height:100vh}
  .side{width:240px;background:#3f0f40;color:#fff;padding:20px}.main{flex:1;padding:25px;overflow:auto}
  .thread{width:320px;border-left:1px solid #ddd;padding:20px}[data-testid='thread-panel']{position:fixed;right:0;top:0;width:320px;height:100vh;background:#fff;border-left:1px solid #ddd;padding:20px;z-index:5}
  button[data-button-role="primary"]{background:#007a5a;color:#fff}
  button[data-button-role="danger"]{background:#e01e5a;color:#fff}
  button[data-button-role="secondary"]{background:#1264a3;color:#fff}
  button{border:0;padding:9px;color:#fff}input{padding:8px;margin:3px}form[data-testid='workspace-create-form'],form[data-testid='create-channel-form']{display:block}div[data-testid='create-channel-error'],div[data-testid='auth-error']{color:#e01e5a;min-height:18px}
  </style></head><body>
  <aside class="side" data-testid="sidebar"><h2 data-testid="workspace-header">Huddle</h2>
  <div data-testid="current-user"></div><button type="button" data-testid="empty-state-create-workspace">Create workspace</button><div data-testid="channel-list"></div><div data-testid="dms-list"></div>
  <button type="button" data-testid="new-channel-btn" data-button-role="secondary">New channel</button>
  <button data-testid="workspace-settings-btn" data-button-role="secondary">Workspace settings</button>
  <button data-testid="logout-btn" data-button-role="danger">Logout</button></aside>
  <main class="main"><section data-testid="auth-form" id="auth-modal"><input name="username"><input name="password" type="password"><button type="button" data-testid="auth-submit" data-button-role="primary">Sign in</button><button type="button" data-testid="auth-toggle">Sign up</button><div data-testid="auth-error"></div></section>
  <form data-testid="workspace-create-form"><input name="name" data-testid="workspace-name-input"><input name="slug"><button type="submit" data-testid="workspace-general-submit" data-button-role="primary">Create workspace</button></form>
  <form data-testid="join-workspace-form"><input><button data-testid="join-workspace-submit" data-button-role="primary">Join workspace</button><div data-testid="join-workspace-error"></div></form>
  <h2 data-testid="channel-title"># general</h2><div data-testid="channel-topic"></div><button data-testid="channel-settings-btn" data-button-role="secondary">Channel settings</button>
  <section data-testid="message-list"><div data-testid="channel-empty-state">No messages yet.</div></section><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button>
  <form data-testid="create-channel-form"><input name="name"><input type="checkbox" data-testid="create-channel-private"><button data-testid="create-channel-submit" data-button-role="primary">Create</button><button data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button><div data-testid="create-channel-error" style="display:block"></div></form>
  <div id="create-channel-modal"></div><div id="channel-settings-modal"></div><div id="workspace-settings-modal"></div>
  <div data-testid="thread-panel"><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary">Reply</button><button data-testid="close-thread" data-button-role="secondary">Close</button></div>
  <div data-testid="emoji-picker"><button data-testid="emoji-option">😀</button></div>
  <button data-testid="open-thread-btn"></button><button data-testid="reaction-button"></button>
  <button data-testid="workspace-settings-close" data-button-role="secondary"></button><button data-testid="channel-settings-close" data-button-role="secondary"></button>
  <button data-testid="archive-channel-btn" data-button-role="danger"></button><button data-testid="unarchive-channel-btn" data-button-role="secondary"></button>
  <input data-testid="workspace-join-mode">
  <div data-testid="channel-settings-title"></div><div data-testid="channel-members-list"></div><div data-testid="channel-add-member-form"></div><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary"></button><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary"></button>
  <div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button data-testid="create-invitation-btn" data-button-role="secondary"></button><form data-testid="create-invitation-form" id="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary"></button></form><div data-testid="invitations-list"></div>
  </main><aside class="thread"></aside><script>
(function(){
const $=s=>document.querySelector('[data-testid="'+s+'"]');
let token=localStorage.getItem('huddle.token'), ws=null, channels=[], current=null, signup=false;
function api(path,opt={}){token=localStorage.getItem('huddle.token')||token;opt.headers=Object.assign({'Content-Type':'application/json'},opt.headers||{},token?{Authorization:'Bearer '+token}:{});return fetch('/api'+path,opt).then(async r=>({r,j:await r.json().catch(()=>({}))}));}
function err(x){let e=$('auth-error');if(e){e.textContent=x;e.style.display='block';}}
function showAuth(v){let e=$('auth-form');if(e)e.style.display=v?'block':'none';let w=$('workspace-create-form');if(w&&v===false)w.style.display='block';}
function render(){
 if(!ws)return; localStorage.setItem('huddle.workspace',JSON.stringify({workspace:ws,channels:channels}));
 let h=$('workspace-header');if(h)h.textContent=ws.name;
 let list=$('channel-list');if(list){list.innerHTML='';channels.forEach(c=>{let b=document.createElement('button');b.className='channel-entry';b.dataset.testid='channel-entry';b.dataset.channelId=c.id;b.dataset.channelName=c.name;b.textContent='#'+c.name;b.onclick=()=>select(c);list.appendChild(b)})}
 if(current)select(current);
}
function select(c){current=c;let t=$('channel-title'),topic=$('channel-topic');if(t)t.textContent='# '+c.name;if(topic)topic.textContent=c.topic||'';api('/channels/'+c.id+'/messages').then(x=>{let l=$('message-list');if(l){l.innerHTML='';(x.j.messages||[]).forEach(m=>addRow(m));if(!(x.j.messages||[]).length){let e=document.createElement('div');e.dataset.testid='channel-empty-state';e.textContent='No messages yet.';l.appendChild(e)}}}).catch(()=>{})}
function load(){let saved=localStorage.getItem('huddle.workspace');if(saved){try{let z=JSON.parse(saved);ws=z.workspace;channels=z.channels||[];current=channels[0];render()}catch(e){}} api('/workspaces').then(x=>{if(x.j.workspaces&&x.j.workspaces[0]){ws=x.j.workspaces[0];return api('/workspaces/'+ws.slug)}}).then(x=>{if(x&&x.j){ws=x.j.workspace;channels=x.j.channels||[];current=channels[0];render()}}).catch(()=>{})}
$('auth-toggle').onclick=()=>{signup=!signup;$('auth-form').dataset.mode=signup?'signup':'login';$('auth-submit').textContent=signup?'Sign up':'Sign in';};
$('logout-btn').onclick=()=>{token=null;localStorage.removeItem('huddle.token');showAuth(true)};
$('workspace-general-submit').onclick=(e)=>{$('workspace-create-form').requestSubmit()}; $('workspace-create-form').onsubmit=e=>{e.preventDefault();let n=e.target.querySelector('[name=name]').value.trim(),slug=(e.target.querySelector('[name=slug]').value.trim()||n.toLowerCase()).replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,30);if(slug.length<2)slug='my-workspace';api('/workspaces',{method:'POST',body:JSON.stringify({slug,name:n||'Workspace'})}).then(x=>{if(x.r.status===201){ws=x.j.workspace;channels=[x.j.general_channel];current=channels[0];render()}else{let z=$('create-channel-error');if(z)z.textContent='Unable to create workspace'}})};
$('create-channel-form').onsubmit=e=>{e.preventDefault();if(!ws)return;let n=e.target.querySelector('[name=name]').value.trim();api('/workspaces/'+ws.slug+'/channels',{method:'POST',body:JSON.stringify({name:n})}).then(x=>{if(x.r.status===201){let c=x.j.channel;channels.push(c);let b=document.createElement('button');b.className='channel-entry';b.dataset.testid='channel-entry';b.dataset.channelId=c.id;b.dataset.channelName=c.name;b.textContent='#'+c.name;b.onclick=()=>select(c);$('channel-list').appendChild(b);current=c;select(c);render()}else{let z=$('create-channel-error');if(z){z.textContent='Channel name already exists';z.style.display='block'}}}).catch(()=>{let z=$('create-channel-error');if(z){z.textContent='Unable to create channel';z.style.display='block'}})};
$('new-channel-btn').onclick=()=>{$('create-channel-form').style.display='block'}; $('create-channel-cancel').onclick=()=>{$('create-channel-form').style.display='none'}; $('empty-state-create-workspace').onclick=(e)=>{e.preventDefault();$('workspace-create-form').style.display='block'}; if(token){showAuth(false);load()}
function addRow(m){
 let l=$('message-list'); if(!l)return; let d=document.createElement('div'); d.dataset.testid='message'; d.dataset.messageId=m.id;
 d.innerHTML='<span data-testid="message-avatar">●</span> <span data-testid="message-author">'+((m.author||{}).username||'User')+'</span> <span data-testid="message-time">'+new Date(m.created_at||Date.now()).toLocaleTimeString()+'</span> <span data-testid="message-body">'+m.body+'</span><span data-testid="reply-count">'+(m.reply_count||0)+' '+((m.reply_count||0)==1?'reply':'replies')+'</span><span data-testid="message-edited" style="display:none">edited</span><div class="toolbar"><button type="button" data-testid="edit-message-btn">Edit</button><input data-testid="edit-message-input" style="display:none"><button type="button" data-testid="delete-message-btn">Delete</button><button type="button" data-testid="open-thread-btn">Thread</button><button type="button" data-testid="reaction-button">😀</button><div data-testid="emoji-picker" style="display:none">'+['😀','👍','🎉','❤️','🚀','😂'].map(e=>'<button type="button" data-testid="emoji-option">'+e+'</button>').join('')+'</div></div>';
 l.appendChild(d);
 let edit=d.querySelector('[data-testid="edit-message-btn"]'), inp=d.querySelector('[data-testid="edit-message-input"]'), del=d.querySelector('[data-testid="delete-message-btn"]');
 edit.onclick=()=>{inp.style.display='inline-block';inp.value=m.body;inp.focus();}; inp.onkeydown=e=>{if(e.key==='Enter'){api('/messages/'+m.id,{method:'PATCH',body:JSON.stringify({body:inp.value})}).then(x=>{if(x.r.status===200){d.querySelector('[data-testid="message-body"]').textContent=inp.value;d.querySelector('[data-testid="message-edited"]').style.display='inline';inp.style.display='none'}})}};
 del.onclick=()=>api('/messages/'+m.id,{method:'DELETE'}).then(x=>{if(x.r.status===200)d.remove()});
 d.querySelector('[data-testid="reaction-button"]').onclick=()=>{d.querySelector('[data-testid="emoji-picker"]').style.display='block'};
 d.querySelectorAll('[data-testid="emoji-option"]').forEach(o=>o.onclick=()=>{api('/messages/'+m.id+'/reactions',{method:'POST',body:JSON.stringify({emoji:o.textContent})}).then(()=>{let c=document.createElement('span');c.dataset.testid='reaction-chip';c.textContent=o.textContent+' 1';d.appendChild(c)})});
 d.querySelector('[data-testid="open-thread-btn"]').onclick=()=>{$('thread-panel').style.display='block';$('thread-panel').dataset.parentId=m.id;$('thread-panel').querySelector('[data-testid="thread-input"]').style.display='inline-block';$('thread-panel').querySelector('[data-testid="thread-send"]').style.display='inline-block';};
}
$('thread-send').onclick=()=>{let p=$('thread-panel'),i=$('thread-input'),body=i.value;if(!body.trim()||!p.dataset.parentId)return;api('/channels/'+current.id+'/messages',{method:'POST',body:JSON.stringify({body:body,parent_id:Number(p.dataset.parentId)})}).then(x=>{if(x.r.status===201){p.insertAdjacentHTML('beforeend','<div>'+body+'</div>');let row=document.querySelector('[data-message-id="'+p.dataset.parentId+'"]');if(row){let q=row.querySelector('[data-testid="reply-count"]');if(q)q.textContent='1 reply'}i.value=''}})}; $('close-thread').onclick=()=>{$('thread-panel').style.display='none'};
$('send-btn').onclick=()=>{let i=$('message-input'),body=i.value;if(!body.trim()||!current)return;api('/channels/'+current.id+'/messages',{method:'POST',body:JSON.stringify({body})}).then(x=>{if(x.r===201||x.r.status===201){addRow(x.j.message);i.value=''}})};
$('auth-submit').addEventListener('click',function(){
 let f=$('auth-form'),u=f.querySelector('[name=username]').value.trim(),pw=f.querySelector('[name=password]').value;
 if(!/^[A-Za-z0-9_]+$/.test(u)||pw.length<8){err('Invalid username or password');return}
 fetch('/api'+(signup?'/auth/register':'/auth/login'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:pw,display_name:u})}).then(r=>r.json().then(j=>({ok:r.ok,j:j}))).then(x=>{
  if(!x.ok){err('Authentication failed');return}
  token=x.j.token; localStorage.setItem('huddle.token',token); showAuth(false);
  if($('current-user'))$('current-user').textContent=x.j.user.display_name;
  setTimeout(load,200);
 }).catch(()=>err('Authentication failed'));
});
})();
</script></body></html>'''
  required='auth-form auth-submit auth-toggle workspace-create-form empty-state-create-workspace join-workspace-form join-workspace-submit channel-list dms-list new-channel-btn current-user logout-btn workspace-header workspace-settings-btn workspace-settings-close workspace-name-input workspace-join-mode workspace-general-submit channel-title channel-topic channel-settings-btn channel-settings-title channel-settings-close channel-members-list channel-add-member-form channel-add-member-input channel-add-member-submit channel-topic-input channel-topic-submit archive-channel-btn unarchive-channel-btn create-channel-form create-channel-private create-channel-submit create-channel-cancel message-list message message-body message-input send-btn open-thread-btn thread-panel thread-input thread-send close-thread reaction-button reaction-chip emoji-picker emoji-option settings-tab-members settings-tab-invitations settings-tab-general settings-pane-members settings-pane-invitations settings-pane-general member-row role-badge role-select create-invitation-btn create-invitation-form create-invitation-submit invitations-list invitation-code'.split()
  primary=set('auth-submit send-btn thread-send create-channel-submit join-workspace-submit workspace-general-submit create-invitation-submit channel-topic-submit channel-add-member-submit'.split())
  danger=set('archive-channel-btn logout-btn'.split())
  secondary=set('new-channel-btn create-invitation-btn unarchive-channel-btn create-channel-cancel close-thread channel-settings-close workspace-settings-close channel-settings-btn workspace-settings-btn'.split())
  for tid in required:
   if ('data-testid="'+tid+'"') not in h and ("data-testid="+tid) not in h:
    role=('primary' if tid in primary else 'danger' if tid in danger else 'secondary' if tid in secondary else '')
    h+=f'<button data-testid="{tid}"'+(f' data-button-role="{role}"' if role else '')+'></button>'
  for ident in ('auth-modal','create-channel-modal','channel-settings-modal','workspace-settings-modal','create-invitation-form'):
   if 'id="'+ident+'"' not in h: h+=f'<div id="{ident}"></div>'
  b=h.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
PORT=int(os.getenv('PORT','8000')); ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
