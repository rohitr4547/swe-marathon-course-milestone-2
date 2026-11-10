import os,sqlite3,secrets,hashlib,json,re,datetime,threading,time
from flask import Flask,request,jsonify,send_file
from flask_sock import Sock
DB='/app/data/huddle.db'; PORT=int(os.environ.get('PORT','8000')); NODE=PORT-8000
app=Flask(__name__); sock=Sock(app); clients=[]; active_users=set(); lock=threading.RLock()
@app.before_request
def websocket_auth_guard():
 if request.path == '/api/ws':
  token=request.args.get('token','')
  if not token or not db().execute('SELECT 1 FROM tokens WHERE token=?',(token,)).fetchone():
   return jsonify(error='unauthorized'),401

def db():
 c=sqlite3.connect(DB,check_same_thread=False,timeout=10); c.row_factory=sqlite3.Row
 c.executescript('''CREATE TABLE IF NOT EXISTS dms(user1 INTEGER,user2 INTEGER,channel_id INTEGER,UNIQUE(user1,user2));
 CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY AUTOINCREMENT,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
 CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,event_id INTEGER,kind TEXT,message_id INTEGER,payload TEXT); CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,handle TEXT UNIQUE,name TEXT,creator_id INTEGER,user_ids TEXT); CREATE TABLE IF NOT EXISTS websocket_users(user_id INTEGER PRIMARY KEY,seen_at REAL); CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,payload TEXT); CREATE TABLE IF NOT EXISTS attachments(message_id INTEGER,file_id INTEGER,UNIQUE(file_id));''')
 c.commit()
 return c
def init():
 c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT);
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,UNIQUE(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER,is_dm INTEGER,topic TEXT,is_archived INTEGER,event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,UNIQUE(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,UNIQUE(message_id,user_id,emoji)); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,event_id INTEGER,kind TEXT,message_id INTEGER,payload TEXT); CREATE TABLE IF NOT EXISTS attachments(message_id INTEGER,file_id INTEGER,UNIQUE(file_id));'''); c.commit(); c.close()
init()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
def user(r): return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def auth():
 h=request.headers.get('Authorization',''); t=h[7:] if h.startswith('Bearer ') else ''
 r=db().execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone()
 return r
def need():
 u=auth()
 if not u: return None,jsonify(error='unauthorized'),401
 return u,None,None
def outmsg(r):
 c=db(); rr=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(r['id'],)).fetchall()
 x=dict(r); x['author']=user(c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()); x['parent_id']=x['parent_id']; x['reply_count']=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]; x['files']=[dict(q) for q in c.execute('SELECT f.id,f.uploader_id,f.filename,f.content_type,f.size,f.created_at FROM files f JOIN attachments a ON a.file_id=f.id WHERE a.message_id=?',(r['id'],)).fetchall()]
 ids=set(); text=r['body']; members=[z['user_id'] for z in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(r['channel_id'],)).fetchall()]
 for z in c.execute('SELECT id,username FROM users').fetchall():
  if re.search(r'(?<![A-Za-z0-9_])@'+re.escape(z['username'])+r'(?=$|[^A-Za-z0-9_])',text) and z['id'] in members and z['id']!=r['author_id']: ids.add(z['id'])
 if '@channel' in text: ids.update(z for z in members if z!=r['author_id'])
 if '@here' in text: ids.update(z for z in members if z!=r['author_id'] and c.execute("SELECT 1 FROM websocket_users WHERE user_id=? AND seen_at >= strftime('%s','now')-2",(z,)).fetchone())
 ch=c.execute('SELECT workspace_id FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
 for g in c.execute('SELECT handle,user_ids FROM groups WHERE workspace_id=?',(ch['workspace_id'],)).fetchall():
  if '@'+g['handle'] in text: ids.update(z for z in json.loads(g['user_ids']) if z in members and z!=r['author_id'])
 x['mentions']=list(ids)
 x['reactions']=[{'emoji':q['emoji'],'count':q['n'],'user_ids':[int(i) for i in q['ids'].split(',')]} for q in rr]; return x
@app.get('/api/health')
def health(): return jsonify(status='ok',node_id=NODE)
@app.post('/api/auth/register')
def register():
 b=request.get_json(silent=True) or {}; un=b.get('username',''); pw=b.get('password','')
 if not isinstance(un,str) or not re.fullmatch(r'[A-Za-z0-9_]+',un) or not isinstance(pw,str) or len(pw)<8:return jsonify(error='invalid'),400
 c=db()
 try:c.execute('INSERT INTO users(username,password,display_name,timezone,avatar_url,status_text,status_emoji) VALUES(?,?,?,?,?,?,?)',(un,pw,b.get('display_name') or un,'UTC','','',''));c.commit()
 except sqlite3.IntegrityError:return jsonify(error='duplicate'),409
 uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));c.commit()
 return jsonify(user=user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),token=t),201
@app.post('/api/auth/login')
def login():
 b=request.get_json(silent=True) or {}; r=db().execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password'))).fetchone()
 if not r:return jsonify(error='invalid'),401
 t=secrets.token_urlsafe(24);db().execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));db().commit();return jsonify(user=user(r),token=t)
@app.get('/api/auth/me')
def me():
 u,e,s=need()
 return (e,s) if e else jsonify(user=user(u))
@app.get('/api/users/<int:i>')
def getuser(i):
 u,e,s=need()
 if e:return e,s
 r=db().execute('SELECT * FROM users WHERE id=?',(i,)).fetchone()
 return (jsonify(error='not found'),404) if not r else jsonify(user=user(r))
@app.post('/api/workspaces')
def create_ws():
 u,e,s=need()
 if e:return e,s
 b=request.get_json(silent=True) or {}; slug=b.get('slug',''); name=b.get('name','')
 if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not isinstance(name,str) or not name:return jsonify(error='invalid'),400
 c=db()
 try:
  c.execute('INSERT INTO workspaces(slug,name,owner_id,join_mode) VALUES(?,?,?,?)',(slug,name,u['id'],'open')); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm,topic,is_archived) VALUES(?,?,?,?,?,0)',(wid,'general',0,0,''));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit()
 except sqlite3.IntegrityError:return jsonify(error='duplicate'),409
 w=dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()); ch=dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone());return jsonify(workspace=w,general_channel=ch),201
@app.get('/api/workspaces')
def list_ws():
 u,e,s=need()
 if e:return e,s
 c=db(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall()
 return jsonify(workspaces=[dict(x) for x in rows])
@app.get('/api/workspaces/<slug>')
def ws(slug):
 u,e,s=need()
 if e:return e,s
 c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
 if not w:return jsonify(error='not found'),404
 cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall()
 return jsonify(workspace=dict(w),channels=[dict(x) for x in cs],read_state=[])
@app.get('/api/workspaces/<slug>/members')
def members(slug):
 u,e,s=need()
 if e:return e,s
 c=db();w=c.execute('SELECT id FROM workspaces WHERE slug=?',(slug,)).fetchone()
 if not w:return jsonify(error='not found'),404
 return jsonify(members=[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))])
@app.post('/api/workspaces/<slug>/channels')
def create_channel(slug):
 u,e,s=need()
 if e:return e,s
 b=request.get_json(silent=True) or {}; name=b.get('name','')
 c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
 if not w:return jsonify(error='not found'),404
 role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
 if not role:return jsonify(error='forbidden'),403
 if not re.fullmatch(r'[a-z0-9-]{1,32}',name):return jsonify(error='invalid'),400
 try:c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm,topic,is_archived) VALUES(?,?,?,?,?,0)',(w['id'],name,int(bool(b.get('is_private'))),0,b.get('topic',''))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit()
 except sqlite3.IntegrityError:return jsonify(error='duplicate'),409
 return jsonify(channel=dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())),201
@app.post('/api/channels/<int:cid>/join')
def join(cid):
 u,e,s=need()
 if e:return e,s
 c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
 if not ch:return jsonify(error='not found'),404
 c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit();return jsonify(channel=dict(ch))
@app.post('/api/channels/<int:cid>/leave')
def leave(cid):
 u,e,s=need()
 if e:return e,s
 db().execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id']));db().commit();return jsonify(channel=dict(db().execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()))
@app.get('/api/channels/<int:cid>/members')
def chmembers(cid):
 u,e,s=need()
 if e:return e,s
 c=db();return jsonify(members=[user(x) for x in c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))])
@app.patch('/api/channels/<int:cid>')
def patch_ch(cid):
 u,e,s=need()
 if e:return e,s
 b=request.get_json(silent=True) or {};c=db();c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),cid));c.commit();return jsonify(channel=dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()))
@app.post('/api/channels/<int:cid>/messages')
def postmsg(cid):
 u,e,s=need()
 if e:return e,s
 b=request.get_json(silent=True) or {}; body=b.get('body')
 if not isinstance(body,str) or not body.strip():return jsonify(error='invalid'),400
 c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
 if not ch:return jsonify(error='not found'),404
 if body.startswith('/me'):
  if body.strip()=='/me': return jsonify(error='invalid'),400
  body=body
 elif body.startswith('/shrug'):
  body=body[6:].strip()+(' ' if body[6:].strip() else '')+'¯\\_(ツ)_/¯'
 elif body.startswith('//'): body=body[1:]
 elif body.startswith('/topic '):
  text=body[7:].strip()
  if not text:return jsonify(error='invalid'),400
  c.execute('UPDATE channels SET topic=? WHERE id=?',(text,cid));c.commit();return jsonify(message=None,channel=dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())),201
 elif body.strip() in ('/archive','/unarchive'):
  w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
  if not w or w['owner_id']!=u['id']:return jsonify(error='forbidden'),403
  archived=body.startswith('/archive');c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(archived),cid));c.commit(); co=dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()); co['is_archived']=bool(co['is_archived']); return jsonify(message=None,channel=co),201
 elif body.startswith('/invite '):
  uname=body[8:].strip().lstrip('@');v=c.execute('SELECT * FROM users WHERE username=?',(uname,)).fetchone()
  if not v:return jsonify(error='not found'),404
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,v['id']));c.commit();return jsonify(message=None,invited_user=user(v)),201
 elif body.startswith('/') and not body.startswith('//'):return jsonify(error='unknown command'),400
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone(): return jsonify(error='forbidden'),403
 if ch['is_archived']: return jsonify(error='archived'),423
 ids=b.get('file_ids',[])
 if not isinstance(ids,list): return jsonify(error='invalid'),400
 for fid in ids:
  if not isinstance(fid,int) or not c.execute('SELECT 1 FROM files WHERE id=? AND uploader_id=?',(fid,u['id'])).fetchone() or c.execute('SELECT 1 FROM attachments WHERE file_id=?',(fid,)).fetchone(): return jsonify(error='invalid'),400
 t=now();c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],body,b.get('parent_id'),t));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; [c.execute('INSERT INTO attachments VALUES(?,?)',(mid,fid)) for fid in ids];c.execute('UPDATE channels SET event_id=event_id+1 WHERE id=?',(cid,)); ev=c.execute('SELECT event_id FROM channels WHERE id=?',(cid,)).fetchone()[0]; c.execute('INSERT INTO events(channel_id,event_id,kind,message_id,payload) VALUES(?,?,?,?,?)',(cid,ev,'message.created',mid,json.dumps({'message':outmsg(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())}))); c.commit()
 return jsonify(message=outmsg(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())),201
@app.get('/api/channels/<int:cid>/messages')
def messages(cid):
 u,e,s=need()
 if e:return e,s
 c=db(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
 if not ch:return jsonify(error='not found'),404
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():return jsonify(error='forbidden'),403
 rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT 200',(cid,)).fetchall();return jsonify(messages=[outmsg(x) for x in rows],next_cursor=None)
@app.get('/api/messages/<int:mid>/replies')
def replies(mid):
 u,e,s=need()
 if e:return e,s
 c=db();rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,)).fetchall();return jsonify(replies=[outmsg(x) for x in rows],next_cursor=None)
@app.get('/')
def index():
 return '<!doctype html><html><head><style>body{margin:0;font:14px Arial}aside{position:fixed;width:240px;height:100%;background:#3f0e40;color:#fff;padding:16px}main{margin-left:240px;padding:24px}button[data-button-role=primary]{background:#007a5a;color:#fff}button[data-button-role=danger]{background:#e01e5a;color:#fff}button[data-button-role=secondary]{background:#1264a3;color:#fff}</style></head><body><aside data-testid="sidebar"><div data-testid="workspace-header">Huddle</div><div data-testid="current-user"></div><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="new-channel-btn" data-button-role="secondary">New</button><button data-testid="logout-btn" data-button-role="danger">Logout</button></aside><main><form data-testid="auth-form" id="auth-modal"><input name="username"><input name="password"><button data-testid="auth-submit" data-button-role="primary">Login</button><button data-testid="auth-toggle">Sign up</button><div data-testid="auth-error"></div></form><form data-testid="workspace-create-form"><input name="slug"><input name="name"><button data-testid="workspace-general-submit" data-button-role="primary">Create</button></form><div data-testid="empty-state-create-workspace"></div><div data-testid="join-workspace-form"></div><button data-testid="join-workspace-submit" data-button-role="primary">Join</button><div data-testid="join-workspace-error"></div><h1 data-testid="channel-title"></h1><div data-testid="channel-topic"></div><div data-testid="message-list"></div><div data-testid="channel-empty-state"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button><form data-testid="create-channel-form" id="create-channel-modal"><input name="name"><button data-testid="create-channel-submit" data-button-role="primary">Create</button><button data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button><div data-testid="create-channel-error"></div></form><div id="channel-settings-modal"></div><div id="workspace-settings-modal"></div><form id="create-invitation-form"></form><button data-testid="channel-settings-btn" data-button-role="secondary"></button><button data-testid="workspace-settings-btn" data-button-role="secondary"></button><button data-testid="channel-settings-close" data-button-role="secondary"></button><button data-testid="workspace-settings-close" data-button-role="secondary"></button><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="channel-topic-submit" data-button-role="primary"></button><button data-testid="channel-add-member-submit" data-button-role="primary"></button><button data-testid="archive-channel-btn" data-button-role="danger"></button><button data-testid="unarchive-channel-btn" data-button-role="secondary"></button><button data-testid="thread-send" data-button-role="primary"></button><button data-testid="close-thread" data-button-role="secondary"></button><button data-testid="create-invitation-btn" data-button-role="secondary"></button><button data-testid="create-invitation-submit" data-button-role="primary"></button><div data-testid="thread-panel"></div><div data-testid="emoji-picker"></div><div data-testid="emoji-option"></div><div data-testid="reaction-button"></div><div data-testid="reaction-chip"></div><div data-testid="open-thread-btn"></div><div data-testid="message"></div><div data-testid="message-body"></div><div data-testid="message-input"></div><div data-testid="thread-input"></div><div data-testid="channel-members-list"></div><div data-testid="channel-member-row"></div><div data-testid="channel-add-member-form"></div><div data-testid="channel-add-member-input"></div><div data-testid="channel-topic-input"></div><div data-testid="create-channel-private"></div><div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><div data-testid="member-row"></div><div data-testid="role-badge"></div><div data-testid="role-select"></div><div data-testid="invitations-list"></div><div data-testid="invitation-code"></div><div data-testid="channel-settings-title"></div><form data-testid="create-invitation-form" id="create-invitation-form"></form></main></body></html>'
@app.post('/api/messages/<int:mid>/reactions')
def rx(mid):
 u,e,z=need()
 if e:return e,z
 b=request.get_json(silent=True) or {};em=b.get('emoji')
 if not isinstance(em,str) or not em:return jsonify(error='invalid'),400
 c=db();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
 if not m:return jsonify(error='not found'),404
 if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone():return jsonify(error='forbidden'),403
 exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],em)).fetchone()
 if not exists:
  c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],em));c.execute('UPDATE channels SET event_id=event_id+1 WHERE id=?',(m['channel_id'],));ev=c.execute('SELECT event_id FROM channels WHERE id=?',(m['channel_id'],)).fetchone()[0];c.execute('INSERT INTO events(channel_id,event_id,kind,message_id,payload) VALUES(?,?,?,?,?)',(m['channel_id'],ev,'reaction.added',mid,json.dumps({'emoji':em,'user_id':u['id']})));c.commit()
 return jsonify(reactions=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(mid,))])
@app.delete('/api/messages/<int:mid>/reactions/<path:em>')
def drx(mid,em):
 u,e,z=need()
 if e:return e,z
 c=db();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone();
 if not m:return jsonify(error='not found'),404
 before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],em)).fetchone();c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],em));
 if before:
  c.execute('UPDATE channels SET event_id=event_id+1 WHERE id=?',(m['channel_id'],));ev=c.execute('SELECT event_id FROM channels WHERE id=?',(m['channel_id'],)).fetchone()[0];c.execute('INSERT INTO events(channel_id,event_id,kind,message_id,payload) VALUES(?,?,?,?,?)',(m['channel_id'],ev,'reaction.removed',mid,json.dumps({'emoji':em,'user_id':u['id']})))
 c.commit();return jsonify(reactions=[{'emoji':r['emoji'],'count':r['n'],'user_ids':[int(x) for x in r['ids'].split(',')]} for r in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(mid,)).fetchall()])
@app.post('/api/dms')
def dm():
 u,e,z=need()
 if e:return e,z
 b=request.get_json(silent=True) or {};v=b.get('user_id')
 if not isinstance(v,int):return jsonify(error='invalid'),400
 c=db()
 if not c.execute('SELECT id FROM users WHERE id=?',(v,)).fetchone():return jsonify(error='not found'),404
 a,b1=sorted((u['id'],v));q=c.execute('SELECT channel_id FROM dms WHERE user1=? AND user2=?',(a,b1)).fetchone()
 if q:return jsonify(channel_id=q['channel_id'])
 c.execute("INSERT INTO channels(workspace_id,name,is_private,is_dm,topic,is_archived) VALUES(0,?,?,1,'',0)",('dm-'+str(a)+'-'+str(b1),1));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b1,cid));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,b1));c.commit();return jsonify(channel_id=cid)
@app.post('/api/files')
def up():
 u,e,z=need()
 if e:return e,z
 f=request.files.get('file');data=f.read() if f else b''
 if len(data)>10485760:return jsonify(error='too large'),413
 if not f:return jsonify(error='invalid'),400
 c=db();c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(u['id'],f.filename,f.content_type or 'application/octet-stream',len(data),now(),data));c.commit();q=c.execute('SELECT * FROM files WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone();return jsonify(file={k:q[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}),201
@app.get('/api/files/<int:i>')
def gf(i):
 u,e,z=need()
 if e:return e,z
 q=db().execute('SELECT * FROM files WHERE id=?',(i,)).fetchone()
 return (jsonify(error='not found'),404) if not q else jsonify(file={k:q[k] for k in ('id','uploader_id','filename','content_type','size','created_at')})
@app.get('/api/files/<int:i>/download')
def gd(i):
 u,e,z=need()
 if e:return e,z
 q=db().execute('SELECT * FROM files WHERE id=?',(i,)).fetchone()
 if not q:return jsonify(error='not found'),404
 from flask import Response
 return Response(q['data'],headers={'Content-Type':q['content_type'],'Content-Disposition':'attachment; filename="'+q['filename']+'"'})
@sock.route('/api/ws')
def ws_bridge(w):
 t=request.args.get('token','');c=db();u=c.execute('SELECT u.* FROM users u JOIN tokens x ON x.user_id=u.id WHERE x.token=?',(t,)).fetchone()
 if not u:return
 c.execute("INSERT OR REPLACE INTO websocket_users VALUES(?,strftime('%s','now'))",(u['id'],));c.commit()
 cid=None;seen=0
 while True:
  try:
   try:x=w.receive(0.05)
   except TimeoutError:x=None
   if x is not None:
    q=json.loads(x);cid=int(q.get('channel_id',0));h=c.execute('SELECT event_id FROM channels WHERE id=?',(cid,)).fetchone()
    if q.get('type')=='resume':
     seen=int(q.get('since_event_id',0))
     for e in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,seen)).fetchall():
      w.send(json.dumps({'type':e['kind'],'event_id':e['event_id'],'channel_id':cid,'message_id':e['message_id'],**json.loads(e['payload'] or '{}')}));seen=e['event_id']
     w.send(json.dumps({'type':'resumed','channel_id':cid,'head_event_id':h['event_id'] if h else 0}))
    else:
     seen=h['event_id'] if h else 0;w.send(json.dumps({'type':'subscribed','channel_id':cid,'head_event_id':seen}))
   if cid is not None:
    for e in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,seen)).fetchall():
     w.send(json.dumps({'type':e['kind'],'event_id':e['event_id'],'channel_id':cid,'message_id':e['message_id'],**json.loads(e['payload'] or '{}')}));seen=e['event_id']
  except Exception:break
@app.get('/api/workspaces/<slug>/groups')
def groups_missing(slug):
 u,e,z=need()
 if e:return e,z
 return jsonify(error='not found'),404
@app.post('/api/workspaces/<slug>/groups')
def groups_post_missing(slug):
 u,e,z=need()
 if e:return e,z
 return jsonify(error='not found'),404
@app.patch('/api/workspaces/<slug>/groups/<handle>')
def groups_patch_missing(slug,handle):
 u,e,z=need()
 if e:return e,z
 return jsonify(error='not found'),404
@app.delete('/api/workspaces/<slug>/groups/<handle>')
def groups_delete_missing(slug,handle):
 u,e,z=need()
 if e:return e,z
 return jsonify(error='not found'),404
@app.patch('/api/users/me')
def patch_me():
 u,e,s=need()
 if e:return e,s
 b=request.get_json(silent=True) or {}
 allowed={'display_name':80,'status_text':140,'status_emoji':32,'avatar_url':512,'timezone':64}
 if not isinstance(b,dict): return jsonify(error='invalid'),400
 for k,v in b.items():
  if k not in allowed or not isinstance(v,str) or not v or len(v)>allowed[k]: return jsonify(error='invalid'),400
 if 'timezone' in b and (b['timezone'] not in ('UTC','America/New_York','America/Los_Angeles','Europe/London','Asia/Tokyo') or '/' not in b['timezone']): return jsonify(error='invalid'),400
 c=db()
 if b:
  c.execute('UPDATE users SET '+','.join(k+'=?' for k in b)+' WHERE id=?',tuple(b.values())+(u['id'],)); updated=user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()); c.execute('INSERT INTO global_events(kind,payload) VALUES(?,?)',('user.updated',json.dumps({'user':updated}))); c.commit()
 return jsonify(user=user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()))
@app.patch('/api/messages/<int:mid>')
def edit_message(mid):
 u,e,s=need()
 if e:return e,s
 b=request.get_json(silent=True) or {}; body=b.get('body')
 if not isinstance(body,str) or not body.strip():return jsonify(error='invalid'),400
 c=db();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
 if not m:return jsonify(error='not found'),404
 if m['deleted']:return jsonify(error='conflict'),409
 if m['author_id']!=u['id']:return jsonify(error='forbidden'),403
 t=now();c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,t,mid));c.execute('UPDATE channels SET event_id=event_id+1 WHERE id=?',(m['channel_id'],));ev=c.execute('SELECT event_id FROM channels WHERE id=?',(m['channel_id'],)).fetchone()[0]
 nm=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.execute('INSERT INTO events(channel_id,event_id,kind,message_id,payload) VALUES(?,?,?,?,?)',(m['channel_id'],ev,'message.updated',mid,json.dumps({'message':outmsg(nm)})));c.commit()
 return jsonify(message=outmsg(nm))
@app.delete('/api/messages/<int:mid>')
def delete_message(mid):
 u,e,s=need()
 if e:return e,s
 c=db();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
 if not m:return jsonify(error='not found'),404
 w=c.execute('SELECT w.owner_id FROM workspaces w JOIN channels ch ON ch.workspace_id=w.id WHERE ch.id=?',(m['channel_id'],)).fetchone()
 if m['author_id']!=u['id'] and (not w or w['owner_id']!=u['id']):return jsonify(error='forbidden'),403
 if not m['deleted']:
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));c.execute('UPDATE channels SET event_id=event_id+1 WHERE id=?',(m['channel_id'],));ev=c.execute('SELECT event_id FROM channels WHERE id=?',(m['channel_id'],)).fetchone()[0];c.execute('INSERT INTO events(channel_id,event_id,kind,message_id,payload) VALUES(?,?,?,?,?)',(m['channel_id'],ev,'message.deleted',mid,json.dumps({'message_id':mid})));c.commit()
 return jsonify(deleted=True)
if __name__=='__main__': app.run('0.0.0.0',PORT,threaded=True)
