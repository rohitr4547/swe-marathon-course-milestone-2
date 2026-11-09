import sqlite3, os, json, hashlib, secrets, re, base64, datetime, argparse, time
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class Tx:
 def __enter__(self): self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
 def __exit__(self,t,v,b):
  self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def tx(): return Tx()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 os.makedirs('/app/data/files',exist_ok=True)
 c=connect()
 c.execute('PRAGMA journal_mode=WAL')
 c.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);""")
 c.commit(); c.close()
def hash_password(pw): return hashlib.sha256(pw.encode()).hexdigest()
def verify_password(pw,stored): return secrets.compare_digest(hash_password(pw),stored)
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
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def get_channel(i):
 c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']
 f={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(f)));return f
def emit_global(c,p): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(p),))
def create_channel_row(c,w,name,private=False,topic=''):
 return c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?) RETURNING *',(w,name,int(private),topic)).fetchone()
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:add_channel_member(c,g['id'],u)
def create_message(user,ch,body,parent=None):
 with tx() as c:
  r=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?) RETURNING *',(ch,user['id'],body,parent,now_iso())).fetchone()
  emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':message_to_json(r)})
 return r
def message_to_json(r):
 c=connect();u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); replies=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']; fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall(); c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[dict(f) for f in fs],'reactions':reactions_for(r['id']),'mentions':[]}
def reactions_for(mid):
 c=connect();rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)).fetchall();c.close();d={}
 for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
 return [{'emoji':e,'count':len(v),'user_ids':v} for e,v in d.items()]
def present_user_ids():
 c=connect()
 rows=c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall()
 c.close()
 return [r['user_id'] for r in rows]
def events_after(ch,s):
 c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,s)).fetchall();c.close();return [json.loads(x['payload']) for x in r]
def head_event_id(ch):
 c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return r['last_event_id'] if r else 0
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--init-db',action='store_true')
 if ap.parse_args().init_db:init_db()
