import sqlite3,os,json,secrets,hashlib,base64,re,time,datetime
from zoneinfo import ZoneInfo
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
class tx:
 def __enter__(self): self.c=connect();self.c.execute('BEGIN IMMEDIATE');return self.c
 def __exit__(self,t,v,b):
  self.c.execute('ROLLBACK' if t else 'COMMIT');self.c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect();c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,body_mentions TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,emoji TEXT,user_id INTEGER,created_at TEXT,PRIMARY KEY(message_id,emoji,user_id));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL)''');
 cols={r['name'] for r in c.execute('PRAGMA table_info(messages)').fetchall()}
 if 'body_mentions' not in cols: c.execute("ALTER TABLE messages ADD COLUMN body_mentions TEXT DEFAULT '[]'")
 c.close()
def hash_password(pw):
 return hashlib.sha256(pw.encode()).hexdigest()
def verify_password(pw,stored): return secrets.compare_digest(hash_password(pw),stored)
def issue_token(c,uid):
 t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));return t
def user_for_token(t):
 if not t:return None
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone();c.close();return r
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return dict(r)
def channel_to_json(r):
 d={k:r[k] for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
 for k in ('is_private','is_dm','is_archived'): d[k]=bool(d[k])
 return d
def get_workspace(s): c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(s,)).fetchone();c.close();return r
def get_channel(i): c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def remove_channel_member(c,ch,u): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u))
def create_channel_row(c,w,name,private=0,topic=''):
 cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic));return cur.lastrowid
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
 for r in c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(w,)): add_channel_member(c,r['id'],u)
def can_read_channel(u,ch):
 if ch['is_dm']:
  c=connect();x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone();c.close();return bool(x)
 return bool(member_role(ch['workspace_id'],u)) and (not ch['is_private'] or _cm(ch['id'],u))
def _cm(ch,u):
 c=connect();x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u)).fetchone();c.close();return x
def require_writable(ch):
 if ch and ch['is_archived']: raise ValueError('ARCHIVED')
def emit_event(c,ch,kind,payload):
 c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(ch,));n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0]
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame)));return frame
def message_to_json(r):
 j=dict(r);j['author']=user_to_json(connect().execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone());j['reply_count']=_count(r['id']);fc=connect();j['files']=[file_to_json(x) for x in fc.execute('SELECT * FROM files WHERE message_id=? ORDER BY id',(r['id'],)).fetchall()];fc.close();j['reactions']=reactions_for(r['id']);j['mentions']=json.loads(r['body_mentions']) if 'body_mentions' in r.keys() and r['body_mentions'] else [];return j
def _count(i):
 c=connect();n=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(i,)).fetchone()[0];c.close();return n
def reactions_for(mid):
 c=connect();rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall();c.close();out=[]
 for r in rows:
  x=next((z for z in out if z['emoji']==r['emoji']),None)
  if not x: x={'emoji':r['emoji'],'count':0,'user_ids':[]};out.append(x)
  x['count']+=1;x['user_ids'].append(r['user_id'])
 return out
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def parse_paging(q,kind):
 lim=int(q.get('limit','50'));return max(1,min(200,lim)),None
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
 try:
  x=json.loads(base64.urlsafe_b64decode(s+'==='));assert x['k']==k and isinstance(x['v'],int);return x['v']
 except: raise ValueError('cursor')
def open_dm(user,other_id):
 with tx() as c:
  a,b=sorted((user['id'],int(other_id)))
  if not c.execute('SELECT id FROM users WHERE id=?',(b,)).fetchone(): return None
  r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
  if r:return r['channel_id']
  cur=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)',());ch=cur.lastrowid
  c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,ch));add_channel_member(c,ch,a);add_channel_member(c,ch,b)
  return ch
def file_to_json(r):
 return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def save_file(user,filename,content_type,data):
 with tx() as c:
  cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(user['id'],filename,content_type,len(data),now_iso()))
  i=cur.lastrowid
  os.makedirs('/app/data/files',exist_ok=True)
  with open('/app/data/files/'+str(i),'wb') as f:f.write(data)
  return c.execute('SELECT * FROM files WHERE id=?',(i,)).fetchone()
def group_to_json(r):
 c=connect();ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id',(r['id'],)).fetchall()];c.close()
 return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
def create_group(user,wid,handle,name,ids):
 if not re.fullmatch(r'[a-z0-9-]{1,32}',handle): raise ValueError('bad')
 with tx() as c:
  if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone(): raise FileExistsError()
  for uid in ids:
   if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(): raise ValueError('member')
  q=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(wid,handle,name,user['id']))
  gid=q.lastrowid
  for uid in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,uid))
  return c.execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone()
def update_group(user,wid,handle,name=None,ids=None):
 with tx() as c:
  g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone()
  if not g:return None
  if g['creator_id']!=user['id'] and c.execute('SELECT owner_id FROM workspaces WHERE id=?',(wid,)).fetchone()['owner_id']!=user['id']:raise PermissionError()
  if name is not None:c.execute('UPDATE groups_ SET name=? WHERE id=?',(name,g['id'],))
  if ids is not None:
   for uid in ids:
    if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone():raise ValueError()
   c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
   for uid in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],uid))
  return c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone()
def touch_presence(conn_id,user_id):
 with tx() as c:c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
 with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
def present_user_ids():
 c=connect();a=[r['user_id'] for r in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall()];c.close();return a
