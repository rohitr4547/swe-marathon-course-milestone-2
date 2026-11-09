import sqlite3, os, json, secrets, hashlib, base64, re, datetime, argparse
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class tx:
 def __enter__(self): self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
 def __exit__(self,t,v,b):
  self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS members(channel_id INTEGER,user_id INTEGER,UNIQUE(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS wmembers(workspace_id INTEGER,user_id INTEGER,role TEXT,UNIQUE(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,UNIQUE(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));'''); c.close()
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
def channel_to_json(r): return dict(r)
def get_workspace(slug):
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
 c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
 c=connect(); r=c.execute('SELECT role FROM wmembers WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO members VALUES(?,?)',(ch,u))
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO wmembers VALUES(?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:add_channel_member(c,g['id'],u)
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def head_event_id(ch):
 c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r['last_event_id'] if r else 0
def events_after(ch,n):
 c=connect(); r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,n)); out=[json.loads(x[0]) for x in r]; c.close(); return out
def create_channel_row(c,w,name,private=False,topic=''):
 cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic)); return cur.lastrowid
def can_read_channel(uid,ch):
 c=connect(); r=c.execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close(); return bool(r)
def require_writable(ch): return not ch['is_archived']
def create_message(user,ch,body,parent=None):
 with tx() as c:
  t=now_iso(); q=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,user['id'],body,parent,t)); mid=q.lastrowid
  r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':message_to_json(r)}); return message_to_json(r)
def resolve_mentions(c, body, channel, author_id):
 ids=[]
 if not isinstance(body,str): return ids
 ch=channel if isinstance(channel,dict) else dict(channel)
 rows=c.execute('SELECT u.id,u.username FROM users u JOIN members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall()
 by={x['username']:x['id'] for x in rows}
 for m in re.finditer(r'(?<![\w@])@([A-Za-z0-9_]+)',body):
  n=m.group(1)
  if n=='channel':
   ids.extend(x['id'] for x in rows if x['id']!=author_id)
  elif n in by and by[n]!=author_id: ids.append(by[n])
 return list(dict.fromkeys(ids))
def message_to_json(r):
 c=connect()
 replies=c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
 mentions=[]
 reactions=[]
 try:
  ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
  mentions=resolve_mentions(c,r['body'],ch,r['author_id'])
  rr=c.execute('SELECT emoji,COUNT(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
  reactions=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[]} for x in rr]
 except Exception: pass
 c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'body':r['body'],'parent_id':r['parent_id'],'created_at':r['created_at'],'edited_at':r['edited_at'],'reply_count':replies,'reactions':reactions,'mentions':mentions,'files':[]}

if __name__=='__main__':
 if '--init-db' in __import__('sys').argv:init_db()
