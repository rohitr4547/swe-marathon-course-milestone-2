import sqlite3,os,json,time,secrets,hashlib,base64,re,argparse
from contextlib import contextmanager
from datetime import datetime,timezone
DB='/app/data/huddle.db'
def connect():
 os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
@contextmanager
def tx():
 c=connect()
 try: c.execute('BEGIN IMMEDIATE'); yield c; c.commit()
 except: c.rollback(); raise
 finally: c.close()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));'''); c.close()
def hp(p):
 s=secrets.token_hex(16); return s+':'+hashlib.sha256((s+p).encode()).hexdigest()
def verify(p,h):
 try: s,x=h.split(':'); return hashlib.sha256((s+p).encode()).hexdigest()==x
 except: return False
def userj(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def chj(r):
 return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def msgj(r):
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'body':r['body'],'parent_id':r['parent_id'],'reply_count':0,'created_at':r['created_at'],'edited_at':r['edited_at'],'author':None,'files':[],'reactions':[],'mentions':[]}
def token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def emit(c,cid,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()[0]
 x={'type':kind,'event_id':n,'channel_id':cid}; x.update(payload); c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(x))); return x
def events_after(cid,n):
 c=connect(); a=[json.loads(x['payload']) for x in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,n))]; c.close(); return a
def head(cid):
 c=connect(); x=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return x[0] if x else 0
if __name__=='__main__':
 ap=argparse.ArgumentParser(); ap.add_argument('--init-db',action='store_true')
 if ap.parse_args().init_db: init_db()
