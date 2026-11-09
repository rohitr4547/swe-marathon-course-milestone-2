import sqlite3,os,secrets,hashlib,re,json,base64,datetime,contextlib
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
@contextlib.contextmanager
def tx():
 c=connect()
 try: c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
 except: c.execute('ROLLBACK'); raise
 finally:c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER);
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));'''); c.close()
def hash_password(p):
 s=secrets.token_hex(16); return s+':'+hashlib.sha256((s+p).encode()).hexdigest()
def verify_password(p,x):
 try:s,h=x.split(':',1);return hashlib.sha256((s+p).encode()).hexdigest()==h
 except:return False
def issue_token(c,uid):
 t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));return t
def user_for_token(t):
 if not t:return None
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone();c.close();return r
def user_json(r): return {'id':r['id'],'username':r['username'],'display_name':r['display_name'],'timezone':'UTC','avatar_url':None,'status_text':'','status_emoji':''}
def ws_json(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':'open'}
def ch_json(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':False,'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def emit(c,cid,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()[0]
 x={'type':kind,'event_id':n,'channel_id':cid,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(x)));return x
def event_head(cid):
 c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone();c.close();return r[0] if r else 0
def events_after(cid,n):
 c=connect();a=[json.loads(x['payload']) for x in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,n))];c.close();return a
def msg_json(r):
 c=connect();u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone();c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':0,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[],'mentions':[]}
def init_message(uid,cid,body,parent=None):
 with tx() as c:
  r=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?) RETURNING *',(cid,uid,body,parent,now_iso())).fetchone()
  m=msg_json(r);emit(c,cid,'message.reply' if parent else 'message.created',{'message':m});return m
if __name__=='__main__': init_db()
