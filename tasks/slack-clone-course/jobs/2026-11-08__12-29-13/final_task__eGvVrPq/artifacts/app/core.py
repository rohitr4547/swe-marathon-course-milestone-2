import sqlite3,hashlib,secrets,datetime,json,base64,re,os,zoneinfo
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
class tx:
 def __enter__(self): self.c=connect();self.c.execute('BEGIN IMMEDIATE');return self
 @property
 def lastrowid(self): return self.c.execute('SELECT last_insert_rowid()').fetchone()[0]
 def execute(self,*a,**k): return self.c.execute(*a,**k)
 def executemany(self,*a,**k): return self.c.executemany(*a,**k)
 def __exit__(self,t,v,b):
  self.c.execute('ROLLBACK' if t else 'COMMIT');self.c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect();c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT);
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
 CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
 CREATE TABLE IF NOT EXISTS groupsx(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
 CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
 CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL); CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT)''');c.close()
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,s): return hash_password(p)==s
def issue_token(c,uid):
 t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));return t
def user_for_token(t):
 if not t:return None
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON u.id=t.user_id WHERE t.token=?',(t,)).fetchone();c.close();return r
def user_to_json(r): return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def workspace_to_json(r): return dict(r)
def channel_json(r):
 d=dict(r)
 for k in ('is_private','is_dm','is_archived'): d[k]=bool(d.get(k,0))
 return d
def emit(c,ch,typ,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
 x={'type':typ,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(x)));return x
def msg_json(c,r):
 d=dict(r);d['author']=user_to_json(c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone());d['parent_id']=r['parent_id'];d['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0];d['edited_at']=r['edited_at'];d['reactions']=reactions(c,r['id']);d['mentions']=mention_ids(c,r['body'],r['channel_id'],r['author_id']);d['files']=[dict(x) for x in c.execute('SELECT * FROM files WHERE message_id=? ORDER BY id',(r['id'],)).fetchall()];return d
def reactions(c,mid):
 out=[]
 for e in c.execute('SELECT DISTINCT emoji FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)):
  ids=[x[0] for x in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=? ORDER BY rowid',(mid,e[0]))];out.append({'emoji':e[0],'count':len(ids),'user_ids':ids})
 return out
def group_json(c,r):
 d={'id':r['id'],'handle':r['handle'],'name':r['name']}
 d['member_user_ids']=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id',(r['id'],))]
 return d
def mention_ids(c,body,ch,author):
 ids=set()
 row=c.execute('SELECT workspace_id FROM channels WHERE id=?',(ch,)).fetchone()
 cr=row[0] if row else None
 for name in re.findall(r'(?<!\\w)@([A-Za-z0-9_-]+)',body):
  if name=='channel':
   ids.update(x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch,)))
  elif name=='here':
   ids.update(x[0] for x in c.execute("SELECT DISTINCT user_id FROM presence WHERE last_seen>? AND user_id IN (SELECT user_id FROM channel_members WHERE channel_id=?)",( __import__('time').time()-3,ch)))
  else:
   gr=c.execute('SELECT id FROM groupsx WHERE workspace_id=? AND handle=?',(cr,name)).fetchone()
   if gr:
    ids.update(x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? AND user_id IN (SELECT user_id FROM channel_members WHERE channel_id=?)',(gr[0],ch)))
   else:
    x=c.execute('SELECT id FROM users WHERE username=? AND id IN (SELECT user_id FROM members WHERE workspace_id=?)',(name,cr)).fetchone()
    if x: ids.add(x[0])
 ids.discard(author)
 ids={x for x in ids if c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,x)).fetchone()}
 return sorted(ids)

if __name__=='__main__':
 import sys
 if '--init-db' in sys.argv:init_db()
