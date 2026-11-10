import sqlite3, os, json, hashlib, secrets, base64, re, datetime, zoneinfo, time
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True)
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); c.execute('PRAGMA journal_mode=WAL'); return c
class tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect()
 for q in ['''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '')''','''CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER)''','''CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open')''','''CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id))''','''CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name))''','''CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id))''','''CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0)''','''CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id))''','''CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji))''','''CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER)''','''CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b))''','''CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL)''']:
  c.execute(q)
 c.execute('CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle))')
 c.execute('CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id))')
 c.close()
def hash_password(pw): return hashlib.sha256(pw.encode()).hexdigest()
def verify_password(pw,stored): return secrets.compare_digest(hash_password(pw),stored)
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def emit_global(c,payload):
 c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def globals_after(since):
 c=connect(); rows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall();c.close()
 return [(x['id'],json.loads(x['payload'])) for x in rows]
def update_profile(user,fields):
 allowed={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512),'timezone':None}
 if not isinstance(fields,dict) or any(k not in allowed or not isinstance(v,str) for k,v in fields.items()): raise ValueError()
 for k,v in fields.items():
  if allowed[k] and not (allowed[k][0]<=len(v)<=allowed[k][1]): raise ValueError()
  if k=='timezone':
   try: zoneinfo.ZoneInfo(v)
   except: raise ValueError()
 with tx() as c:
  if fields:
   c.execute('UPDATE users SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),user['id']))
   u=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
   emit_global(c,{'type':'user.updated','user':user_to_json(u)})
 return u
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def get_workspace(slug): 
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
 c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
 c=connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def head_event_id(ch):
 c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r[0] if r else 0
def events_after(ch,s):
 c=connect(); x=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,s)); out=[json.loads(r[0]) for r in x]; c.close(); return out
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w,u,role))
def create_channel_row(c,w,name,private=0,topic=''):
 cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,private,topic)); return cur.lastrowid
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def present_user_ids():
 c=connect(); rows=c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall();c.close();return [x[0] for x in rows]
def resolve_mentions(c,body,channel,author_id):
 ids=set()
 for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)',body):
  if name=='channel':
   ids.update(x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(channel['id'],author_id)))
  elif name=='here':
   present=set(present_user_ids())
   rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(channel['id'],author_id)).fetchall()
   ids.update(x[0] for x in rows if x[0] in present)
  else:
   for x in c.execute('SELECT u.id FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=? AND u.username=? AND u.id!=?',(channel['id'],name,author_id)): ids.add(x[0])
   for x in c.execute('SELECT gm.user_id FROM groups g JOIN group_members gm ON gm.group_id=g.id WHERE g.workspace_id=? AND g.handle=? AND gm.user_id!=? AND gm.user_id IN (SELECT user_id FROM channel_members WHERE channel_id=?)',(channel['workspace_id'],name,author_id,channel['id'])): ids.add(x[0])
 return list(ids)
def message_to_json(r):
 c=connect()
 qs=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(r['id'],)).fetchall()
 fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()
 replies=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
 c.close()
 grouped={}
 for x in qs: grouped.setdefault(x['emoji'],[]).append(x['user_id'])
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[dict(x) for x in fs],'reactions':[{'emoji':k,'count':len(v),'user_ids':v} for k,v in grouped.items()],'mentions':[]}
if __name__=='__main__' and '--init-db' in __import__('sys').argv: init_db()

def touch_presence(conn_id,user_id):
 with tx() as c: c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
 with tx() as c: c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
