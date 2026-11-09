import sqlite3,os,secrets,hashlib,base64,json,re,datetime,contextlib
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
@contextlib.contextmanager
def tx():
 c=connect()
 try:c.execute('BEGIN IMMEDIATE');yield c;c.execute('COMMIT')
 except: c.execute('ROLLBACK');raise
 finally:c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 os.makedirs('/app/data',exist_ok=True);c=connect();c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER);
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,topic TEXT DEFAULT '',archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));''');c.close()
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,s): return hash_password(p)==s
def issue_token(c,u): t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u));return t
def user_for_token(t):
 if not t:return None
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone();c.close();return r
def uj(r): return {'id':r['id'],'username':r['username'],'display_name':r['display_name'],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''}
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
 f={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(f)));return f
def head(ch):
 c=connect();x=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return x[0] if x else 0
def events_after(ch,n):
 c=connect();x=[json.loads(r[0]) for r in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,n))];c.close();return x
def cjson(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':False,'topic':r['topic'] or '','is_archived':bool(r['archived'])}
def mjson(r):
 c=connect(); a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':uj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':0,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[],'mentions':[]}
def init_message(c,u,ch,b,parent=None):
 t=now_iso();x=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,u,b,parent,t));r=c.execute('SELECT * FROM messages WHERE id=?',(x.lastrowid,)).fetchone();f=emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':mjson(r)});return r,f
if __name__=='__main__':
 init_db()
