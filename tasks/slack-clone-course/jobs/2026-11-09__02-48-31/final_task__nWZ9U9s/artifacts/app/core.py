import sqlite3, os, json, hashlib, secrets, base64, re, datetime, time
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class tx:
 def __enter__(self):
  self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
 def __exit__(self,t,v,b):
  self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 for s in ['''users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "")''','''tokens(token TEXT PRIMARY KEY,user_id INTEGER)''','''workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open")''','''workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id))''','''channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name))''','''channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id))''','''messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0)''','''events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id))''','''reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji))''','''pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT)''','''read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,mention_count INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id))''','''invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at REAL,max_uses INTEGER,uses INTEGER DEFAULT 0)''','''groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle))''','''group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id))''','''dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b))''','''files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER)''']:
  c.execute('CREATE TABLE IF NOT EXISTS '+s)
 c.execute('CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL)')
 c.execute('CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT)')
 c.close()
def hash_password(pw):
 salt=secrets.token_hex(16); return salt+'$'+hashlib.sha256((salt+pw).encode()).hexdigest()
def verify_password(pw,stored):
 try: salt,h=stored.split('$',1); return hashlib.sha256((salt+pw).encode()).hexdigest()==h
 except: return False
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(token):
 if not token:return None
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r): return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def workspace_to_json(r): return {k:r[k] for k in ['id','slug','name','owner_id','join_mode']}
def get_workspace(slug):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def channel_to_json(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def get_channel(i):
 c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def emit_global(c,payload):
 c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def globals_after(since):
 c=connect(); rows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)); x=[(r['id'],json.loads(r['payload'])) for r in rows]; c.close(); return x
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']; x={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(x)));return x
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def remove_channel_member(c,ch,u): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u))
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(w,u,role)); g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:add_channel_member(c,g['id'],u)
def create_channel_row(c,w,name,private=False,topic=''):
 cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic));return cur.lastrowid
def can_read_channel(u,ch):
 c=connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone(); role=member_role(ch['workspace_id'],u) if ch['workspace_id'] else None;c.close();return bool(x or (not ch['is_private'] and role and role!='guest'))
def require_writable(ch):
 if ch and ch['is_archived']: raise ValueError('archived')
def create_message(u,ch,body,parent=None):
 with tx() as c:
  r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone(); require_writable(r)
  c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,u,body,parent,now_iso()));mid= c.execute('SELECT last_insert_rowid()').fetchone()[0]; emit_event(c,ch,'message.reply' if parent else 'message.created',{'message_id':mid,'message':{'id':mid,'channel_id':ch,'author_id':u,'body':body,'parent_id':parent}}); return c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
def mention_ids(mid,body,ch,author):
 c=connect(); out=[]
 members=[x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch,))]
 for name in re.findall(r'(?<![\w@.])@(\w+)',body):
  if name=='channel':
   vals=members
  elif name=='here':
   vals=[x['user_id'] for x in c.execute('SELECT user_id FROM presence WHERE last_seen>?',(time.time()-3,))]
  else:
   g=c.execute('SELECT id FROM groups WHERE handle=?',(name,)).fetchone()
   if g: vals=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]
   else:
    q=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone(); vals=[q['id']] if q else []
  for v in vals:
   if v!=author and v in members and v not in out: out.append(v)
 c.close();return out
def message_to_json(r):
 c=connect();a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone();n=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n'];c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[dict(x) for x in connect().execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE message_id=?',(r['id'],))],
'reactions':reactions_for(r['id']),'mentions':mention_ids(r['id'],r['body'],r['channel_id'],r['author_id'])}
def head_event_id(ch):
 c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return r['last_event_id'] if r else 0
def events_after(ch,s):
 c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,s));x=[json.loads(a['payload']) for a in r];c.close();return x
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
 try:
  x=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4))); assert x['k']==k and isinstance(x['v'],int);return x['v']
 except: raise ValueError('cursor')
def parse_paging(q,k):
 if 'page'in q or 'offset'in q: raise ValueError('paging')
 n=int(q.get('limit',50)); assert 1<=n<=200; return n,decode_cursor(k,q['cursor']) if q.get('cursor') else 0
def reactions_for(mid):
 c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,));d={}
 for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
 c.close();return [{'emoji':k,'count':len(v),'user_ids':v} for k,v in d.items()]
init_db()
if __name__=='__main__': init_db()
