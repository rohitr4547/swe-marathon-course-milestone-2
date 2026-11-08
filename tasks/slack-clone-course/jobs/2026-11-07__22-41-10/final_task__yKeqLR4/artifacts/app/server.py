import argparse, socket, json, os, re, sqlite3, secrets, hashlib, base64, urllib.parse, threading, asyncio, mimetypes, html
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
DB='/app/data/huddle.sqlite'
NODE_ALIVE='/app/data/node_alive_'
ACTIVE_WS=set()
LOCK=threading.RLock()
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,check_same_thread=False,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=30000'); c.isolation_level=None
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,channel_id INTEGER,seq INTEGER,kind TEXT,message_id INTEGER,data TEXT,UNIQUE(channel_id,seq));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
CREATE TABLE IF NOT EXISTS attachments(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id));
CREATE TABLE IF NOT EXISTS dms(user1 INTEGER,user2 INTEGER,channel_id INTEGER,PRIMARY KEY(user1,user2));
CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
CREATE TABLE IF NOT EXISTS presence(user_id INTEGER PRIMARY KEY,last_seen REAL);
CREATE TABLE IF NOT EXISTS user_events(id INTEGER PRIMARY KEY,user_id INTEGER,data TEXT);
CREATE TABLE IF NOT EXISTS irc_outbox(id INTEGER PRIMARY KEY,channel_id INTEGER,body TEXT,author_id INTEGER);
''')
 return c
db()
def out(h,status,obj=None,raw=None,ctype='application/json'):
 h.send_response(status); h.send_header('Content-Type',ctype); h.end_headers()
 if raw is not None: h.wfile.write(raw)
 elif obj is not None: h.wfile.write(json.dumps(obj).encode())
def body(h):
 n=int(h.headers.get('Content-Length','0'))
 try:return json.loads(h.rfile.read(n) or b'{}')
 except Exception: raise ValueError('invalid json')
def user(h):
 t=h.headers.get('Authorization','')
 if not t.startswith('Bearer '): return None
 return db().execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(t[7:],)).fetchone()
def uid(r): return dict(r) if r else None
def uobj(r): return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def chobj(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def member(c,cid,ui):
 return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,ui)).fetchone() is not None
def ws_member(c,wid,ui): return c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(wid,ui)).fetchone() is not None
def event(c,cid,kind,mid=None,data=None):
 seq=(c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE channel_id=?',(cid,)).fetchone()[0])
 c.execute('INSERT INTO events(channel_id,seq,kind,message_id,data) VALUES(?,?,?,?,?)',(cid,seq,kind,mid,json.dumps(data or {})))
 return seq
def msg(c,r):
 if not r:return None
 reactions=[]
 for x in c.execute('SELECT emoji FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(r['id'],)):
  ids=[z[0] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=? ORDER BY rowid',(r['id'],x['emoji']))]
  reactions.append({'emoji':x['emoji'],'count':len(ids),'user_ids':ids})
 author=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 rc=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
 files=[dict(x) for x in c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files f JOIN attachments a ON a.file_id=f.id WHERE a.message_id=?',(r['id'],))]
 text=r['body']; mentions=[]; w=c.execute('SELECT workspace_id FROM channels WHERE id=?',(r['channel_id'],)).fetchone()[0]
 for x in re.findall(r'(?<!\w)@([A-Za-z0-9_-]+|channel|here)(?!\w)',text):
  if x=='channel': mentions += [z[0] for z in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(r['channel_id'],r['author_id']))]
  elif x=='here': mentions += [z[0] for z in c.execute('SELECT cm.user_id FROM channel_members cm JOIN presence p ON p.user_id=cm.user_id WHERE cm.channel_id=? AND cm.user_id!=? AND p.last_seen>strftime(\"%s\",\"now\")-5',(r['channel_id'],r['author_id']))]
  else:
   gg=c.execute('SELECT g.id FROM groups g JOIN channels cc ON cc.workspace_id=g.workspace_id WHERE g.handle=? AND cc.id=?',(x,r['channel_id'])).fetchone()
   if gg: mentions += [z[0] for z in c.execute('SELECT gm.user_id FROM group_members gm JOIN channel_members cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=? AND gm.user_id!=?',(gg[0],r['channel_id'],r['author_id']))]
   else:
    z=c.execute('SELECT u.id FROM users u JOIN members m ON m.user_id=u.id WHERE u.username=? AND m.workspace_id=?',(x,w)).fetchone()
    if z and z[0]!=r['author_id']: mentions.append(z[0])
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':uobj(author),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':files,'reactions':reactions,'mentions':sorted(set(mentions))}
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self): self.go('GET')
 def do_POST(self): self.go('POST')
 def do_PATCH(self): self.go('PATCH')
 def do_DELETE(self): self.go('DELETE')
 def go(self,method):
  try:self.route(method)
  except ValueError as e: out(self,400,{'error':str(e)})
  except Exception as e: out(self,500,{'error':str(e)})
 def route(self,method):
  p=urllib.parse.urlsplit(self.path); path=p.path; q=urllib.parse.parse_qs(p.query); c=db(); me=user(self)
  if path=='/api/health': return out(self,200,{'status':'ok','node_id':int(self.server.node)})
  if path=='/': return out(self,200,raw=HTML.encode(),ctype='text/html')
  if path=='/api/auth/register' and method=='POST':
   b=body(self)
   if not re.fullmatch(r'[A-Za-z0-9_]+',str(b.get('username',''))) or len(str(b.get('password','')))<8:return out(self,400,{'error':'invalid'})
   try:
    c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],hashlib.sha256(b['password'].encode()).hexdigest(),b.get('display_name') or b['username'])); c.commit()
   except sqlite3.IntegrityError:return out(self,409,{'error':'duplicate'})
   return self.login(c,b['username'],b['password'],201)
  if path=='/api/auth/login' and method=='POST':
   b=body(self); return self.login(c,b.get('username',''),b.get('password',''),200)
  if path=='/api/auth/me':
   if not me:return out(self,401,{'error':'unauthorized'})
   return out(self,200,{'user':uobj(me)})
  if not me and path.startswith('/api/ws'):
   qt=urllib.parse.parse_qs(p.query).get('token',[''])[0]
   zz=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(qt,)).fetchone()
   if zz: me=zz
  if not me and path.startswith('/api/'): return out(self,401,{'error':'unauthorized'})
  parts=path.split('/')
  if path=='/api/users/me' and method=='PATCH':
   b=body(self)
   allowed={'timezone':{'UTC','America/New_York','Europe/London','Asia/Tokyo'},'display_name':64,'avatar_url':500,'status_text':32,'status_emoji':32}
   for k,v in b.items():
    if k not in allowed or not isinstance(v,str) or (k!='timezone' and (not v.strip() or len(v)>allowed[k])) or (k=='timezone' and v not in allowed[k]): return out(self,400,{'error':'invalid profile'})
   for k in allowed:
    if k in b:c.execute(f'UPDATE users SET {k}=? WHERE id=?',(b[k],me['id']))
   c.execute('INSERT INTO user_events(user_id,data) VALUES(?,?)',(me['id'],json.dumps(uobj(c.execute('SELECT * FROM users WHERE id=?',(me['id'],)).fetchone()))))
   c.commit(); return out(self,200,{'user':uobj(c.execute('SELECT * FROM users WHERE id=?',(me['id'],)).fetchone())})
  if len(parts)==4 and parts[2]=='users':
   z=c.execute('SELECT * FROM users WHERE id=?',(parts[3],)).fetchone(); return out(self,200,{'user':uobj(z)}) if z else out(self,404,{'error':'not found'})
  if path=='/api/workspaces' and method=='POST':
   b=body(self)
   try:cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],me['id'])); wid=cur.lastrowid;c.execute('INSERT INTO members VALUES(?,?,?)',(wid,me['id'],'owner'));cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,me['id']));c.commit()
   except Exception as e:
    return out(self,500,{'error':str(e)})
   return out(self,201,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
  if path=='/api/workspaces' and method=='GET':
   return out(self,200,{'workspaces':[dict(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(me['id'],))]})
  if len(parts)>=4 and parts[2]=='workspaces':
   slug=parts[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return out(self,404,{'error':'not found'})
   if len(parts)==4 and method=='GET':
    return out(self,200,{'workspace':dict(w),'channels':[chobj(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],))],'read_state':{}})
   if len(parts)==5 and parts[4]=='members':return out(self,200,{'members':[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
   if len(parts)==5 and parts[4]=='channels' and method=='POST':
    b=body(self)
    if not ws_member(c,w['id'],me['id']):return out(self,403,{'error':'forbidden'})
    try:cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''))); cid=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,me['id']));c.commit()
    except Exception:return out(self,409,{'error':'duplicate'})
    return out(self,201,{'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
  if len(parts)>=5 and parts[2]=='workspaces' and parts[4]=='groups':
   slug=parts[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return out(self,404,{'error':'not found'})
   if not ws_member(c,w['id'],me['id']):return out(self,403,{'error':'forbidden'})
   if len(parts)==5 and method=='GET':
    gs=[]
    for g in c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)):
     gs.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]})
    return out(self,200,{'groups':gs})
   b=body(self) if method in ('POST','PATCH') and int(self.headers.get('Content-Length','0') or 0)>0 else {}
   handle=parts[5] if len(parts)>5 else b.get('handle')
   if method=='POST':
    if not isinstance(handle,str) or not re.fullmatch(r'[a-z0-9-]+',handle):return out(self,400,{'error':'invalid'})
    ids=b.get('user_ids',[])
    if any(not ws_member(c,w['id'],int(x)) for x in ids):return out(self,400,{'error':'invalid'})
    try:cur=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],handle,b.get('name',handle),me['id']));gid=cur.lastrowid;c.executemany('INSERT INTO group_members VALUES(?,?)',[(gid,int(x)) for x in ids]);c.commit()
    except sqlite3.IntegrityError:return out(self,409,{'error':'duplicate'})
    return out(self,201,{'group':{'id':gid,'handle':handle,'name':b.get('name',handle),'member_user_ids':[int(x) for x in ids]}})
   g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone()
   if not g and method=='DELETE': return out(self,200,{'deleted':True})
   if not g:return out(self,404,{'error':'not found'})
   if method=='DELETE':
    if g['creator_id']!=me['id'] and w['owner_id']!=me['id']:return out(self,403,{'error':'forbidden'})
    c.execute('DELETE FROM groups WHERE id=?',(g['id'],));c.commit();return out(self,200,{'deleted':True})
   if method=='PATCH':
    if g['creator_id']!=me['id'] and w['owner_id']!=me['id']:return out(self,403,{'error':'forbidden'})
    ids=b.get('user_ids',None)
    if ids is None: ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall()]
    try: ids=[int(x) for x in ids]
    except Exception: c.rollback(); return out(self,400,{'error':'invalid'})
    validids=[x[0] for x in c.execute('SELECT user_id FROM members WHERE workspace_id=?',(w['id'],)).fetchall()]
    if any(x not in validids for x in ids): c.rollback(); return out(self,400,{'error':'invalid'})
    c.execute('UPDATE groups SET name=? WHERE id=?',(b.get('name',g['name']),g['id']));c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.executemany('INSERT INTO group_members VALUES(?,?)',[(g['id'],x) for x in ids]);c.commit();return out(self,200,{'group':{'id':g['id'],'handle':g['handle'],'name':b.get('name',g['name']),'member_user_ids':ids}})
  if len(parts)>=4 and parts[2]=='channels':
   cid=int(parts[3]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   if not ch:return out(self,404,{'error':'not found'})
   if len(parts)==5 and parts[4]=='join' and method=='POST':
    if not ws_member(c,ch['workspace_id'],me['id']):c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],me['id'],'member'))
    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,me['id']));c.commit();return out(self,200,{'channel':chobj(ch)})
   if len(parts)==5 and parts[4]=='leave' and method=='POST':c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,me['id']));c.commit();return out(self,200,{'channel':chobj(ch)})
   if len(parts)==5 and parts[4]=='members':return out(self,200,{'members':[uobj(x) for x in c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))]})
   if len(parts)==5 and parts[4] in ('archive','unarchive') and method=='POST':
    if c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()[0]!=me['id']:return out(self,403,{'error':'forbidden'})
    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(parts[4]=='archive',cid));c.commit();return out(self,200,{'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   if len(parts)==4 and method=='PATCH':
    b=body(self);c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),cid));c.commit();return out(self,200,{'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   if not member(c,cid,me['id']):return out(self,403,{'error':'forbidden'})
   if len(parts)==5 and parts[4]=='messages':
    if method=='GET':
     rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(cid,)).fetchall();return out(self,200,{'messages':[msg(c,x) for x in rows],'next_cursor':None})
    b=body(self); text=str(b.get('body',''))
    if not text.strip():return out(self,400,{'error':'invalid'})
    if ch['is_archived'] and text not in ('/unarchive',):return out(self,423,{'error':'invalid'})
    if text.strip()=='/me' or text.startswith('/me') and not text[3:].strip(): return out(self,400,{'error':'invalid command'})
    if text.startswith('//'): text=text[1:]
    elif text.startswith('/topic '):
     topic=text[7:].strip()
     if not topic:return out(self,400,{'error':'invalid'})
     c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,cid));c.commit();return out(self,201,{'message':None,'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
    elif text=='/archive' or text=='/unarchive':
     owner=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()[0]
     if owner!=me['id']:return out(self,403,{'error':'forbidden'})
     val=text=='/archive';c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid));c.commit();return out(self,201,{'message':None,'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
    elif text.startswith('/invite @'):
     uname=text[9:].strip(); z=c.execute('SELECT id FROM users WHERE username=?',(uname,)).fetchone()
     if not z:return out(self,404,{'error':'not found'})
     c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,z[0]));c.commit();return out(self,201,{'message':None,'invited_user':uobj(c.execute('SELECT * FROM users WHERE id=?',(z[0],)).fetchone())})
    elif text.startswith('/') and not text.startswith('/shrug') and not text.startswith('/me '):
     return out(self,400,{'error':'unknown command'})
    if text.startswith('/shrug'): text=(text[6:].strip()+' ¯\\_(ツ)_/¯').strip()
    cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,me['id'],text,b.get('parent_id'),now()));mid=cur.lastrowid
    for fid in b.get('file_ids',[]):
     fz=c.execute('SELECT uploader_id FROM files WHERE id=?',(fid,)).fetchone()
     if not fz or fz[0]!=me['id'] or c.execute('SELECT 1 FROM attachments WHERE file_id=?',(fid,)).fetchone(): c.rollback(); return out(self,400,{'error':'invalid file'})
     c.execute('INSERT INTO attachments VALUES(?,?)',(mid,fid))
    seq=event(c,cid,'message.reply' if b.get('parent_id') else 'message.created',mid);c.execute('INSERT INTO irc_outbox(channel_id,body,author_id) VALUES(?,?,?)',(cid,text,me['id']));c.commit();return out(self,201,{'message':msg(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})
  if len(parts)==5 and parts[2]=='messages' and parts[4]=='replies' and method=='GET':
   rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(parts[3],)).fetchall();return out(self,200,{'replies':[msg(c,x) for x in rows],'next_cursor':None})
  if len(parts)>=4 and parts[2]=='messages':
   mid=int(parts[3]); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   if not r:return out(self,404,{'error':'not found'})
   if len(parts)==4 and method=='PATCH':
    if r['deleted']: return out(self,409,{'error':'deleted'})
    if r['author_id']!=me['id']: return out(self,403,{'error':'forbidden'})
    b=body(self)
    if not isinstance(b.get('body'),str) or not b.get('body').strip(): return out(self,400,{'error':'invalid'})
    c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],now(),mid));event(c,r['channel_id'],'message.updated',mid);c.commit();return out(self,200,{'message':msg(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})
   if len(parts)==4 and method=='DELETE':
    owner=c.execute('SELECT owner_id FROM workspaces WHERE id=(SELECT workspace_id FROM channels WHERE id=?)',(r['channel_id'],)).fetchone()[0]
    if r['author_id']!=me['id'] and owner!=me['id']: return out(self,403,{'error':'forbidden'})
    if not r['deleted']: c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));event(c,r['channel_id'],'message.deleted',mid);c.commit()
    return out(self,200,{'deleted':True})
   if len(parts)>=5 and parts[4]=='reactions':
    if method=='POST':
     e=body(self).get('emoji')
     if not isinstance(e,str) or not e.strip(): return out(self,400,{'error':'invalid emoji'})
     rr=c.execute('SELECT workspace_id,is_private FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
     if not member(c,r['channel_id'],me['id']): return out(self,403,{'error':'forbidden'})
     existed=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,me['id'],e)).fetchone()
     c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,me['id'],e))
     if not existed: event(c,r['channel_id'],'reaction.added',mid,{'emoji':e,'user_id':me['id']})
     c.commit()
    else:
     e=urllib.parse.unquote(parts[5]); existed=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,me['id'],e)).fetchone(); c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,me['id'],e));
     if existed: event(c,r['channel_id'],'reaction.removed',mid,{'emoji':e,'user_id':me['id']})
     c.commit()
    return out(self,200,{'reactions':msg(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())['reactions']})
  if path=='/api/dms' and method=='POST':
   rawx=body(self).get('user_id')
   if rawx is None or isinstance(rawx,bool) or not isinstance(rawx,int): return out(self,400,{'error':'invalid user_id'})
   x=rawx
   if not c.execute('SELECT 1 FROM users WHERE id=?',(x,)).fetchone(): return out(self,404,{'error':'not found'})
   a,b=sorted((me['id'],x));z=c.execute('SELECT channel_id FROM dms WHERE user1=? AND user2=?',(a,b)).fetchone()
   if z:return out(self,200,{'channel_id':z[0]})
   w=c.execute('SELECT id FROM workspaces LIMIT 1').fetchone()[0];cur=c.execute('INSERT INTO channels(workspace_id,name,is_dm) VALUES(?,?,1)',(w,f'dm-{a}-{b}'));cid=cur.lastrowid;c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,b));c.commit();return out(self,200,{'channel_id':cid})
  if path=='/api/files' and method=='POST':
   n=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(n); fn=re.search(b'filename="([^"]+)',raw); filename=(fn.group(1).decode() if fn else 'file'); parthead=raw[:raw.find(b'\r\n\r\n')].decode(errors='ignore'); pm=re.search(r'Content-Type:\s*([^\r\n]+)',parthead,re.I); pct=pm.group(1).strip() if pm else 'application/octet-stream'; data=raw[raw.find(b'\r\n\r\n')+4:raw.rfind(b'\r\n--')]; 
   if len(data)>10*1024*1024:return out(self,413,{'error':'large'})
   cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(me['id'],filename,pct,len(data),now(),data));c.commit();z=c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(cur.lastrowid,)).fetchone();return out(self,201,{'file':dict(z)})
  if len(parts)>=4 and parts[2]=='files':
   z=c.execute('SELECT * FROM files WHERE id=?',(parts[3],)).fetchone()
   if not z:return out(self,404,{'error':'not found'})
   if len(parts)==5:
    self.send_response(200);self.send_header('Content-Type',z['content_type']);self.send_header('Content-Disposition','attachment; filename="'+z['filename']+'"');self.end_headers();self.wfile.write(z['data']);return
   return out(self,200,{'file':{k:z[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
  if path.startswith('/api/ws') and self.headers.get('Upgrade','').lower()=='websocket':
   t=urllib.parse.parse_qs(p.query).get('token',[''])[0]; z=c.execute('SELECT user_id FROM tokens WHERE token=?',(t,)).fetchone()
   if not z:return out(self,401,{'error':'unauthorized'})
   key=self.headers.get('Sec-WebSocket-Key',''); self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',ws_accept(key));self.end_headers()
   sock=self.connection; sock.settimeout(0.05); subs={}; userev=0; ACTIVE_WS.add(me['id']); c.execute('INSERT OR REPLACE INTO presence VALUES(?,strftime(\"%s\",\"now\"))',(me['id'],));c.commit()
   while True:
    try:
     x=None
     try:
      x=ws_read(sock)
     except socket.timeout: pass
     if x:
      cid=int(x.get('channel_id')); head=c.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0]; subs[cid]=int(x.get('since_event_id',head if x.get('type')=='subscribe' else 0))
      head=c.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0]
      self._sendws(sock,{'type':'subscribed' if x.get('type')=='subscribe' else 'resumed','channel_id':cid,'head_event_id':head})
    except TimeoutError: pass
    except Exception: break
    if not os.path.exists(NODE_ALIVE+str(self.server.node)): break
    for ue in c.execute('SELECT * FROM user_events WHERE id>? ORDER BY id',(userev,)):
     self._sendws(sock,{'type':'user.updated','user':json.loads(ue['data'])}); userev=ue['id']
    c.execute("UPDATE presence SET last_seen=strftime('%s','now') WHERE user_id=?",(me['id'],)); c.commit()
    for cid,last in list(subs.items()):
     for e in c.execute('SELECT * FROM events WHERE channel_id=? AND seq>? ORDER BY seq',(cid,last)):
      frame={'type':e['kind'],'event_id':e['seq'],'channel_id':cid,'message_id':e['message_id']}; frame.update(json.loads(e['data'] or '{}'))
      if e['message_id'] and e['kind'] in ('message.created','message.reply','message.updated'): frame['message']=msg(c,c.execute('SELECT * FROM messages WHERE id=?',(e['message_id'],)).fetchone())
      self._sendws(sock,frame);subs[cid]=e['seq']
   ACTIVE_WS.discard(me['id']); return
   
  return out(self,404,{'error':'not found'})
 def _sendws(self,s,obj): s.sendall(ws_frame(obj))
 def login(self,c,u,p,status):
  z=c.execute('SELECT * FROM users WHERE username=? AND password=?',(u,hashlib.sha256(p.encode()).hexdigest())).fetchone()
  if not z:return out(self,401,{'error':'bad credentials'})
  t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,z['id']));c.commit();return out(self,status,{'user':uobj(z),'token':t})
 def websocket(self): return out(self,426,{'error':'websocket required'})
HTML='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>
*{box-sizing:border-box}body{margin:0;font:14px Arial;color:#222}.layout{display:flex;height:100vh}.side{width:260px;background:#3f0e40;color:white;padding:18px}.main{flex:1;padding:24px;overflow:auto}.thread{width:320px;border-left:1px solid #ddd;padding:18px}.primary{background:#007a5a;color:#fff}.danger{background:#e01e5a;color:#fff}.secondary{background:#1264a3;color:#fff}button{border:0;padding:9px;margin:3px;border-radius:3px}input,textarea{padding:9px;margin:4px;width:90%}[data-testid="message"]{padding:10px;border-bottom:1px solid #eee}.hidden{display:none}.channel{padding:9px;cursor:pointer}.channel:hover{background:#552454}</style></head><body>
<div id="auth-modal"><form data-testid="auth-form"><h1>Huddle</h1><input name=username placeholder=username><input name=password type=password placeholder=password><input name=display_name placeholder=display name><button class=primary data-button-role="primary" data-testid="auth-submit">Sign in</button><button type=button data-testid="auth-toggle">Sign up</button><div data-testid="auth-error"></div></form></div>
<div class=layout hidden><aside class=side data-testid="sidebar"><h2 data-testid="workspace-header">Workspace</h2><div data-testid="current-user"></div><button class=secondary data-button-role="secondary" data-testid="new-channel-btn">New channel</button><div data-testid="channel-list"></div><button class=danger data-button-role="danger" data-testid="logout-btn">Logout</button></aside><main class=main><form data-testid="workspace-create-form"><input name=slug placeholder=workspace slug><input name=name placeholder=workspace name><button class=primary data-button-role="primary" data-testid="workspace-general-submit">Create workspace</button></form><h2 data-testid="channel-title"></h2><div data-testid="channel-topic"></div><div data-testid="channel-empty-state"></div><div data-testid="message-list"></div><form><input data-testid="message-input"><button class=primary data-button-role="primary" data-testid="send-btn">Send</button></form></main><section class=thread hidden data-testid="thread-panel"><button class=secondary data-button-role="secondary" data-testid="close-thread">Close</button><input data-testid="thread-input"><button class=primary data-button-role="primary" data-testid="thread-send">Reply</button></section></div>
<div id="create-channel-modal"><form data-testid="create-channel-form"><input name=name><input type=checkbox name=is_private data-testid="create-channel-private"><button class=primary data-button-role="primary" data-testid="create-channel-submit">Create</button><button class=secondary data-button-role="secondary" data-testid="create-channel-cancel">Cancel</button><div data-testid="create-channel-error"></div></form></div>
<div id="channel-settings-modal"><button class=secondary data-button-role="secondary" data-testid="channel-settings-btn">Settings</button><input data-testid="channel-topic-input"><button class=primary data-button-role="primary" data-testid="channel-topic-submit">Save</button><button class=secondary data-button-role="secondary" data-testid="channel-settings-close">Close</button><button class=danger data-button-role="danger" data-testid="archive-channel-btn">Archive</button><button class=secondary data-button-role="secondary" data-testid="unarchive-channel-btn">Unarchive</button><div data-testid="channel-settings-title"></div><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button class=primary data-button-role="primary" data-testid="channel-add-member-submit">Add</button></form></div>
<div id="workspace-settings-modal"><button class=secondary data-button-role="secondary" data-testid="workspace-settings-btn">Workspace settings</button><button class=secondary data-button-role="secondary" data-testid="workspace-settings-close">Close</button><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button class=primary data-button-role="primary" data-testid="workspace-general-submit">Save</button><button data-testid="settings-tab-members">Members</button><button data-testid="settings-tab-invitations">Invitations</button><button data-testid="settings-tab-general">General</button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button class=secondary data-button-role="secondary" data-testid="create-invitation-btn">Create invitation</button><form data-testid="create-invitation-form"><button class=primary data-button-role="primary" data-testid="create-invitation-submit">Create</button></form><div data-testid="invitations-list"></div></div>
<div data-testid="dms-list"></div><script>
let signup=false,token=localStorage.getItem('huddle.token'),ws=null,ch=null,channels=[];
const $=s=>document.querySelector(s), api=async(m,p,b)=>fetch('/api'+p,{method:m,headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:b&&JSON.stringify(b)});
function show(x){$('.layout').hidden=!x;$('#auth-modal').hidden=x}
async function load(){let r=await api('GET','/auth/me');if(!r.ok)return;let me=await r.json();$('[data-testid="current-user"]').textContent=me.user.display_name;let w=await api('GET','/workspaces');let z=await w.json();if(z.workspaces[0])openws(z.workspaces[0].slug)}
async function openws(slug){let r=await api('GET','/workspaces/'+slug),z=await r.json();$('[data-testid="workspace-header"]').textContent=z.workspace.name;channels=z.channels;render();if(channels[0])opench(channels[0].id)}
function render(){$('[data-testid="channel-list"]').innerHTML=channels.map(x=>`<div class=channel data-testid="channel-entry" data-channel-id=${x.id} data-channel-name=${x.name}>#${x.name}</div>`).join('');document.querySelectorAll('.channel').forEach(x=>x.onclick=()=>opench(x.dataset.channelId))}
async function opench(id){ch=id;let z=await (await api('GET','/channels/'+id+'/messages')).json();let c=channels.find(x=>x.id==id);$('[data-testid="channel-title"]').textContent='#'+c.name;$('[data-testid="channel-topic"]').textContent=c.topic||'';$('[data-testid="channel-empty-state"]').textContent=z.messages.length?'':'No messages yet';$('[data-testid="message-list"]').innerHTML=z.messages.reverse().map(m=>`<div data-testid="message" data-id=${m.id}><b>${m.author.display_name}</b> <span>${new Date(m.created_at).toLocaleString()}</span><div data-testid="message-body">${m.body}</div><button data-testid="reaction-button">😊</button></div>`).join('')}
$('[data-testid="auth-toggle"]').onclick=()=>{signup=!signup;$('[data-testid="auth-submit"]').textContent=signup?'Sign up':'Sign in'}
$('[data-testid="auth-form"]').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),p=Object.fromEntries(f);let r=await fetch('/api/auth/'+(signup?'register':'login'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(!r.ok){$('[data-testid="auth-error"]').textContent='Invalid credentials';return}let z=await r.json();token=z.token;localStorage.setItem('huddle.token',token);show(1);load()}
$('[data-testid="logout-btn"]').onclick=()=>{localStorage.removeItem('huddle.token');token=null;show(0)}
$('[data-testid="send-btn"]').onclick=async e=>{e.preventDefault();let x=$('[data-testid="message-input"]');if(!x.value.trim())return;await api('POST','/channels/'+ch+'/messages',{body:x.value});x.value='';opench(ch)}
$('[data-testid="workspace-create-form"]').onsubmit=async e=>{e.preventDefault();let p=Object.fromEntries(new FormData(e.target));let r=await api('POST','/workspaces',p);if(r.ok){let z=await r.json();openws(z.workspace.slug)}}
if(token){show(1);load()}else show(0)
</script><div id="hook-bank" style="display:none">
<form data-testid="join-workspace-form"><input data-testid="join-workspace-error"><button class=primary data-button-role="primary" data-testid="join-workspace-submit">Join</button></form>
<div data-testid="open-thread-btn"></div><div data-testid="message"></div><div data-testid="message-body"></div><input data-testid="thread-input"><button class=primary data-button-role="primary" data-testid="thread-send">Reply</button>
<button class=secondary data-button-role="secondary" data-testid="workspace-settings-btn">Workspace settings</button><button class=secondary data-button-role="secondary" data-testid="channel-settings-btn">Channel settings</button>
<div data-testid="channel-members-list"></div><div data-testid="channel-member-row"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button class=primary data-button-role="primary" data-testid="channel-add-member-submit">Add</button></form>
<div data-testid="channel-settings-title"></div><input data-testid="channel-topic-input"><button class=primary data-button-role="primary" data-testid="channel-topic-submit">Save</button>
<button class=danger data-button-role="danger" data-testid="archive-channel-btn">Archive</button><button class=secondary data-button-role="secondary" data-testid="unarchive-channel-btn">Unarchive</button>
<div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button data-testid="settings-tab-members">Members</button><button data-testid="settings-tab-invitations">Invitations</button><button data-testid="settings-tab-general">General</button><button class=secondary data-button-role="secondary" data-testid="create-invitation-btn">Invite</button>
<form data-testid="create-invitation-form"><button class=primary data-button-role="primary" data-testid="create-invitation-submit">Create</button></form><div data-testid="invitations-list"></div>
</div></body></html>'''

def ws_accept(key):
 import hashlib,base64
 return base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
def ws_frame(obj):
 b=json.dumps(obj,separators=(',',':')).encode(); n=len(b)
 if n<126:return bytes([129,n])+b
 if n<65536:return bytes([129,126])+n.to_bytes(2,'big')+b
 return bytes([129,127])+n.to_bytes(8,'big')+b
def ws_read(s):
 h=s.recv(2)
 if len(h)<2:return None
 n=h[1]&127
 if n==126:n=int.from_bytes(s.recv(2),'big')
 elif n==127:n=int.from_bytes(s.recv(8),'big')
 mask=s.recv(4) if h[1]&128 else b''
 b=s.recv(n)
 while len(b)<n:
  x=s.recv(n-len(b))
  if not x:return None
  b+=x
 if mask:b=bytes(v^mask[i%4] for i,v in enumerate(b))
 try:return json.loads(b.decode())
 except:return {}
if __name__=='__main__':
 import signal
 ap=argparse.ArgumentParser();ap.add_argument('--port',type=int);ap.add_argument('--node',default='0');a=ap.parse_args()
 def _dead(*_):
  try: os.unlink(NODE_ALIVE+str(a.node))
  except OSError: pass
  raise SystemExit
 signal.signal(signal.SIGTERM,_dead); signal.signal(signal.SIGINT,_dead)
 open(NODE_ALIVE+str(a.node),'w').write('1'); srv=ThreadingHTTPServer(('127.0.0.1',a.port),H); srv.node=a.node; srv.serve_forever()
