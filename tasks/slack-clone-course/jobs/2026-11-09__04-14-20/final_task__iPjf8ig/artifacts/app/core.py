import sqlite3, os, json, secrets, hashlib, base64, re, datetime, time, argparse
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class tx:
    def __enter__(self): self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 stmts=['''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT)''','''CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER)''','''CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open')''','''CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id))''','''CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name))''','''CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id))''','''CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]')''','''CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,created_at TEXT,PRIMARY KEY(message_id,user_id,emoji))''','''CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id))''','''CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT)''','''CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,mention_count INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id))''','''CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,expires_at TEXT,created_at TEXT)''','''CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b))''','''CREATE TABLE IF NOT EXISTS groups_tbl(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT)''','''CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT)''','''CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL)''']
 for s in stmts:c.execute(s)
 c.close()
def hash_password(pw):
 s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,st):
 try:s,h=st.split('$',1); return hashlib.sha256((s+pw).encode()).hexdigest()==h
 except:return False
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
 return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def get_workspace(slug):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def get_channel(i):
 c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']; d={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(d)));return d
def emit_global(c,p): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(p),))
def add_channel_member(c,ch,u):
 x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u)).rowcount
 if x: emit_event(c,ch,'member.joined',{'user_id':u})
 return bool(x)
def remove_channel_member(c,ch,u):
 x=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u)).rowcount
 if x: emit_event(c,ch,'member.left',{'user_id':u})
 return bool(x)
def create_channel_row(c,w,name,private,topic,creator):
 cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic));i=cur.lastrowid
 if creator:add_channel_member(c,i,creator)
 return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(w,u,role));g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:add_channel_member(c,g['id'],u)
def join_channel(user, channel_id):
 ch=get_channel(channel_id)
 if not ch: raise LookupError('not found')
 if ch['is_dm']: raise PermissionError('forbidden')
 role=member_role(ch['workspace_id'],user['id'])
 if ch['is_private']:
  c=connect(); existing=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel_id,user['id'])).fetchone(); c.close()
  if not existing: raise PermissionError('forbidden')
  return ch
  return ch
 if role is None:
  w=get_workspace_by_id(ch['workspace_id'])
  if not w or w['join_mode']!='open': raise PermissionError('forbidden')
  with tx() as c:
   add_workspace_member(c,ch['workspace_id'],user['id'],'member')
 elif role=='guest': raise PermissionError('forbidden')
 with tx() as c: add_channel_member(c,channel_id,user['id'])
 return get_channel(channel_id)
def get_workspace_by_id(i):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE id=?',(i,)).fetchone();c.close();return r
def can_read_channel(u,ch):
 c=connect(); own=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone(); role=member_role(ch['workspace_id'],u) if ch['workspace_id'] else None;c.close()
 return bool(own or (not ch['is_private'] and role and role!='guest'))
def require_writable(ch):
 if ch and ch['is_archived']: raise ValueError('archived')
def head_event_id(i):
 c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(i,)).fetchone();c.close();return r['last_event_id'] if r else 0
def events_after(i,s):
 c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(i,s));a=[json.loads(x['payload']) for x in r];c.close();return a
def encode_cursor(k,v):return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
 try:d=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4)));assert d['k']==k and isinstance(d['v'],int);return d['v']
 except:raise ValueError('invalid cursor')
def parse_paging(q,k):
 n=int(q.get('limit','50')); 
 if n<1 or n>200:raise ValueError('invalid limit')
 return n,decode_cursor(k,q['cursor']) if q.get('cursor') else None
def message_to_json(r):
 c=connect();u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone();rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall();rep=c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n'];c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rep,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rs],'mentions':json.loads(r['mentions'] or '[]')}
def create_message(user,ch,body,parent=None):
 if not isinstance(body,str) or not body.strip():raise ValueError('message body required')
 with tx() as c:
  r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone();require_writable(r);cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch,user['id'],body,parent,now_iso(),json.dumps([])));i=cur.lastrowid;r=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone();ev=emit_event(c,ch,'message.reply' if parent else 'message.created',{'message_id':i})
 result=message_to_json(r)
 with tx() as c:
  ev['message']=result; c.execute('UPDATE events SET payload=? WHERE channel_id=? AND event_id=?',(json.dumps(ev),ch,ev['event_id']))
 return result
if __name__=='__main__':
 if '--init-db' in os.sys.argv:init_db()
