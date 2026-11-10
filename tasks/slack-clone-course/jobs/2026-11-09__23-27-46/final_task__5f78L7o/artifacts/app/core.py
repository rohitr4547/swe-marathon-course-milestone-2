import sqlite3, os, json, secrets, hashlib, re, base64
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def conn():
    os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
def tx():
    c=conn(); c.execute('BEGIN IMMEDIATE')
    class C:
        def __enter__(s): return c
        def __exit__(s,t,v,b):
            c.execute('ROLLBACK' if t else 'COMMIT'); c.close()
    return C()
def init_db():
 c=conn(); c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));'''); c.close()
def user_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def token_user(tok):
 c=conn(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(tok,)).fetchone(); c.close(); return r
def register(username,password,display):
 with tx() as c:
  cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(username,hashlib.sha256(password.encode()).hexdigest(),display or username)); uid=cur.lastrowid
  tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid)); return c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(),tok
def login(username,password):
 c=conn(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(username,hashlib.sha256(password.encode()).hexdigest())).fetchone()
 if not r:return None
 tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id'])); c.close(); return r,tok
def emit(c,cid,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()[0]
 frame={'type':kind,'event_id':n,'channel_id':cid,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return frame
def memberships(c,uid,cid):
 return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone()
def channel_json(r): return dict(r)
def reactions(c,mid):
 rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)).fetchall(); d={}
 for x in rows:d.setdefault(x['emoji'],[]).append(x['user_id'])
 return [{'emoji':k,'count':len(v),'user_ids':v} for k,v in d.items()]
def msg_json(c,r):
 d=dict(r); d['author']=user_json(c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()); d['parent_id']=r['parent_id']; d['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]; d['reactions']=reactions(c,r['id']); d['mentions']=json.loads(r['mentions'] or '[]'); d['files']=[]; return d
def make_message(uid,cid,body,parent=None):
 with tx() as c:
  ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  if not ch:return None,'404'
  if ch['is_archived']:return None,'423'
  if not memberships(c,uid,cid):return None,'403'
  mentions=[] 
  for m in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
   u=c.execute('SELECT id FROM users WHERE username=?',(m,)).fetchone()
   if u and u[0]!=uid and memberships(c,u[0],cid) and u[0] not in mentions:mentions.append(u[0])
  t=now_iso(); mid=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,uid,body,parent,t,json.dumps(mentions))).lastrowid
  r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); frame=emit(c,cid,'message.reply' if parent else 'message.created',{'message':msg_json(c,r)}); return msg_json(c,r),frame
if __name__=='__main__':
 import sys
 if '--init-db' in sys.argv:init_db()
