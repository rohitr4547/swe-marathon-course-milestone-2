import sqlite3, os, hashlib, secrets, json, base64, re, datetime, contextlib
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000'); c.execute('PRAGMA foreign_keys=ON'); return c
@contextlib.contextmanager
def tx():
    c=connect()
    try: c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
    except: c.execute('ROLLBACK'); raise
    finally: c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
 CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));'''); c.close()
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,s): return secrets.compare_digest(hash_password(p),s)
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
 if not t:return None
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON u.id=t.user_id WHERE t.token=?',(t,)).fetchone();c.close();return r
def user_to_json(r): return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def workspace_to_json(r): return dict(r)
def channel_to_json(r): 
 d=dict(r); d['is_private']=bool(d['is_private']);d['is_dm']=bool(d['is_dm']);d['is_archived']=bool(d['is_archived']);return d
def get_workspace(slug):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def get_channel(i):
 c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u))
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame)));return frame
def create_message(u,ch,body,parent_id=None):
 with tx() as c:
  r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  if not r: raise LookupError()
  if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u['id'])).fetchone(): raise PermissionError()
  mentions=resolve_mentions(c,body,r,u['id']); ts=now_iso()
  x=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch,u['id'],body,parent_id,ts,json.dumps(mentions)))
  row=c.execute('SELECT * FROM messages WHERE id=?',(x.lastrowid,)).fetchone()
  emit_event(c,ch,'message.reply' if parent_id else 'message.created',{'message':message_to_json(row)})
  return message_to_json(row)
def resolve_mentions(c,body,ch,author):
 out=[]
 for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body):
  if name=='channel':
   ids=[x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],author))]
  else:
   q=c.execute('SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE u.username=? AND m.workspace_id=?',(name,ch['workspace_id'])).fetchone();ids=[q[0]] if q and q[0]!=author else []
  for i in ids:
   if i not in out:out.append(i)
 return out
def head_event_id(channel_id):
 c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(channel_id,)).fetchone(); c.close()
 return int(r[0]) if r else 0
def events_after(channel_id,since):
 c=connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(channel_id,since)).fetchall(); c.close()
 return [dict(x) for x in rows]
def reactions_for(mid):
 c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)).fetchall();c.close();d={}
 for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
 return [{'emoji':k,'count':len(v),'user_ids':v} for k,v in d.items()]
def message_to_json(r):
 d=dict(r);d['author']=user_to_json(connect().execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone());d['parent_id']=r['parent_id']; c=connect(); d['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]; c.close(); d['mentions']=json.loads(r['mentions'] or '[]');d['reactions']=reactions_for(r['id']);return d
if __name__=='__main__': init_db()
