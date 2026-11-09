import sqlite3, os, hashlib, secrets, json, base64, re, datetime, contextlib
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
@contextlib.contextmanager
def tx():
 c=connect()
 try: c.execute('BEGIN IMMEDIATE'); yield c; c.commit()
 except: c.rollback(); raise
 finally: c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 os.makedirs('/app/data/files',exist_ok=True)
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));'''); c.close()
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def user_to_json(r): return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def workspace_to_json(r): return dict(r)
def channel_to_json(r): d=dict(r); d['is_private']=bool(d['is_private']); d['is_dm']=bool(d['is_dm']); d['is_archived']=bool(d['is_archived']); return d
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('insert into tokens values(?,?)',(t,uid)); return t
def user_for_token(t):
 if not t:return None
 c=connect(); r=c.execute('select u.* from users u join tokens t on t.user_id=u.id where t.token=?',(t,)).fetchone(); c.close(); return r
def get_workspace(slug):
 c=connect(); r=c.execute('select * from workspaces where slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
 c=connect(); r=c.execute('select * from channels where id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
 c=connect(); r=c.execute('select role from members where workspace_id=? and user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def emit_event(c,ch,kind,payload):
 n=c.execute('update channels set last_event_id=last_event_id+1 where id=? returning last_event_id',(ch,)).fetchone()[0]
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('insert into events values(?,?,?)',(ch,n,json.dumps(frame))); return frame
def add_member(c,w,u,role='member'):
 c.execute('insert or ignore into members values(?,?,?)',(w,u,role))
def channel_json_msg(c,r):
 d=dict(r)
 d['author']=user_to_json(c.execute('select * from users where id=?',(r['author_id'],)).fetchone())
 d['parent_id']=r['parent_id']
 d['reply_count']=c.execute('select count(*) from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()[0]
 d['mentions']=json.loads(r['mentions'] or '[]')
 d['reactions']=[]
 groups=c.execute('select emoji,count(*) n from reactions where message_id=? group by emoji',(r['id'],)).fetchall()
 for x in groups:
  ids=[z[0] for z in c.execute('select user_id from reactions where message_id=? and emoji=?',(r['id'],x['emoji'])).fetchall()]
  d['reactions'].append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
 return d
def create_message(uid,ch,body,parent=None):
 with tx() as c:
  r=c.execute('select * from channels where id=?',(ch,)).fetchone(); now=now_iso(); mentions=[]
  for x in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
   z=c.execute('select u.id from users u join members m on m.user_id=u.id join channels q on q.workspace_id=m.workspace_id where q.id=? and u.username=?',(ch,x)).fetchone()
   if z and z[0]!=uid and z[0] not in mentions: mentions.append(z[0])
  cur=c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at,mentions) values(?,?,?,?,?,?)',(ch,uid,body,parent,now,json.dumps(mentions))); mid=cur.lastrowid; m=channel_json_msg(c,c.execute('select * from messages where id=?',(mid,)).fetchone()); emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':m}); return m
if __name__=='__main__':
 if '--init-db' in __import__('sys').argv:init_db()
