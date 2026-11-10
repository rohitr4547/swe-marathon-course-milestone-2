import sqlite3, os, hashlib, time, secrets, json, re, datetime, base64
from contextlib import contextmanager
from zoneinfo import ZoneInfo
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row
 c.execute('PRAGMA busy_timeout=10000'); return c
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
 CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
 CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
 CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
 CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
 CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
 CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
 CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL)'''); c.close()
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,h): return secrets.compare_digest(hash_password(p),h)
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
 return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def workspace_to_json(r): return dict(r)
def channel_to_json(r): return dict(r)
def get_workspace(slug):
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
 c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
 c=connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u))
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame)))
 return frame
def emit_global(c,p): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(p),))
def msg_json(c,r):
 d=dict(r); d['author']=user_to_json(c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()); d['parent_id']=r['parent_id']; d['mentions']=[]
 fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()
 d['files']=[dict(x) for x in fs]
 rs=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(r['id'],)).fetchall(); groups={}
 for x in rs: groups.setdefault(x['emoji'],[]).append(x['user_id'])
 d['reactions']=[{'emoji':e,'count':len(v),'user_ids':v} for e,v in groups.items()]
 d['mentions']=resolve_mentions(c,r['body'],r['channel_id'],r['author_id'])
 d['reply_count']=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']; return d
def create_message(uid,ch,body,parent=None):
 with tx() as c:
  r=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,uid,body,parent,now_iso()))
  row=c.execute('SELECT * FROM messages WHERE id=?',(r.lastrowid,)).fetchone(); j=msg_json(c,row); emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':j}); return j
def resolve_mentions(c,body,ch,author):
 ids=set(); wr=c.execute('SELECT workspace_id FROM channels WHERE id=?',(ch,)).fetchone()['workspace_id']
 members={x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch,))}
 for name in re.findall(r'(?<!\w)@([A-Za-z0-9_-]+)',body):
  if name=='channel': ids.update(members)
  elif name=='here':
   ids.update(x['user_id'] for x in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)) if x['user_id'] in members)
  else:
   g=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(wr,name)).fetchone()
   if g: ids.update(x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)) if x['user_id'] in members)
   else:
    x=c.execute('SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE u.username=? AND m.workspace_id=?',(name,wr)).fetchone()
    if x and x['id'] in members: ids.add(x['id'])
 ids.discard(author); return list(ids)
def group_json(c,g):
 d=dict(g); d['member_user_ids']=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]; return d

def touch_presence(conn_id,user_id):
 with tx() as c: c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
 with tx() as c: c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
def present_user_ids():
 c=connect(); r=[x['user_id'] for x in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall()]; c.close(); return r

if __name__ == '__main__':
 import sys
 if '--init-db' in sys.argv:
  init_db()
