import sqlite3, os, json, hashlib, secrets, re, base64, datetime, time
from contextlib import contextmanager
from zoneinfo import ZoneInfo
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
@contextmanager
def tx():
 c=connect()
 try: c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
 except: c.execute('ROLLBACK'); raise
 finally: c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 os.makedirs('/app/data/files',exist_ok=True); c=connect(); c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS wmembers(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER, UNIQUE(workspace_id,handle));
CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL)'''); c.close()
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,s): return secrets.compare_digest(hash_password(p),s)
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
 if not t:return None
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return dict(r)
def channel_to_json(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def get_workspace(slug):
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
 c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
 c=connect(); r=c.execute('SELECT role FROM wmembers WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO wmembers VALUES(?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:c.execute('INSERT OR IGNORE INTO members VALUES(?,?)',(g['id'],u))
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO members VALUES(?,?)',(ch,u))
def remove_channel_member(c,ch,u): c.execute('DELETE FROM members WHERE channel_id=? AND user_id=?',(ch,u))
def can_read_channel(u,ch):
 c=connect(); r=c.execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone(); c.close(); return bool(r)
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
 d={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(d))); return d
def emit_global(c,p): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(p),))
def head_event_id(ch):
 c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r[0] if r else 0
def events_after(ch,n):
 c=connect(); a=[json.loads(r[0]) for r in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,n))]; c.close(); return a
def globals_after(n):
 c=connect(); a=[(r[0],json.loads(r[1])) for r in c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(n,))]; c.close(); return a
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
 try:
  x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); assert x['k']==k and isinstance(x['v'],int); return x['v']
 except: raise ValueError()
def parse_paging(q,k):
 try:n=int(q.get('limit',50)); assert 1<=n<=200
 except: raise ValueError()
 return n,decode_cursor(k,q['cursor']) if q.get('cursor') else None
def user_obj(uid):
 c=connect();r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close();return user_to_json(r) if r else None
def message_to_json(r):
 c=connect(); a=user_obj(r['author_id']); rs=[]
 for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(r['id'],)):
  rs.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]})
 files=[dict(x) for x in c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE message_id=? ORDER BY id',(r['id'],))]; c.close(); return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':a,'body':r['body'],'parent_id':r['parent_id'],'reply_count':0,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':files,'reactions':rs,'mentions':[]}
def touch_presence(cid,u):
 with tx() as c:c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(cid,u,time.time()))
def drop_presence(cid):
 with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(cid,))
def present_user_ids():
 c=connect();a=[r[0] for r in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))];c.close();return a
if __name__=='__main__' and '--init-db' in __import__('sys').argv:init_db()
