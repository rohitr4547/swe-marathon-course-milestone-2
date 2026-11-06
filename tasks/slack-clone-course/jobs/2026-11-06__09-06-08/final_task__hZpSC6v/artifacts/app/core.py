import sqlite3, os, json, hashlib, secrets, base64, re, datetime, contextlib, argparse, time
from zoneinfo import ZoneInfo
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
@contextlib.contextmanager
def tx():
 c=connect()
 try: c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
 except: c.execute('ROLLBACK'); raise
 finally: c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 os.makedirs('/app/data/files',exist_ok=True)
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 schema='''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,created_at TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE TABLE IF NOT EXISTS groups_tbl(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);'''
 for x in schema.split(';'):
  if x.strip(): c.execute(x)
 c.close()
def hash_password(pw):
 s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
 try: s,h=stored.split('$'); return hashlib.sha256((s+pw).encode()).hexdigest()==h
 except: return False
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(token):
 if not token:return None
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
 return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def message_to_json(r):
 c=connect(); author=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 reps=c.execute('SELECT emoji,group_concat(user_id) ids,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY min(rowid)', (r['id'],)).fetchall()
 fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()
 rc=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
 mentions=json.loads(r['mentions']) if 'mentions' in r.keys() and r['mentions'] else []
 c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(author),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[file_to_json(x) for x in fs],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in reps],'mentions':mentions}
def file_to_json(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def get_workspace(slug): 
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(cid):
 c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def member_role(wid,uid):
 c=connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,wid,uid,role='member'):
 c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(wid,uid,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
 if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],uid))
def create_channel_row(c,wid,name,private=False,topic=''):
 cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic)); return cur.lastrowid
def add_channel_member(c,cid,uid): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid))
def remove_channel_member(c,cid,uid): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid))
def can_read_channel(uid,ch):
 if ch['is_dm']: 
  c=connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone();c.close();return bool(x)
 return bool(member_role(ch['workspace_id'],uid)) and (not ch['is_private'] or _cm(ch['id'],uid))
def _cm(cid,uid):
 c=connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone(); c.close(); return bool(x)
def require_writable(ch): return not ch['is_archived']
def emit_event(c,cid,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()['last_event_id']
 frame={'type':kind,'event_id':n,'channel_id':cid,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return frame
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(cid):
 c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone();c.close();return r['last_event_id'] if r else 0
def events_after(cid,since):
 c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall();c.close();return [json.loads(x['payload']) for x in r]
def globals_after(since):
 c=connect();r=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall();c.close();return [(x['id'],json.loads(x['payload'])) for x in r]
def encode_cursor(kind,v): return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':int(v)}).encode()).decode().rstrip('=')
def decode_cursor(kind,s):
 try:
  x=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4))); assert x['k']==kind and isinstance(x['v'],int); return x['v']
 except: raise ValueError()
def parse_paging(q,kind):
 try: lim=int(q.get('limit','50')); assert 1<=lim<=200
 except: raise ValueError()
 cur=q.get('cursor'); return lim,(decode_cursor(kind,cur) if cur else None)
def resolve_mentions(c,body,ch,author):
 ids=set(); wid=ch['workspace_id']; 
 for name in re.findall(r'(?<!\w)@([a-zA-Z0-9_-]+)',body):
  if name=='channel':
   ids.update(x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)))
  elif name=='here': ids.update(x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)) if x['user_id'] in present_user_ids())
  else:
   u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
   if u and _cm(ch['id'],u['id']): ids.add(u['id'])
   g=c.execute('SELECT id FROM groups_tbl WHERE workspace_id=? AND handle=?',(wid,name)).fetchone() if wid else None
   if g:
    cm={x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],))}
    ids.update(x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)) if x['user_id'] in cm)
 ids.discard(author); return sorted(ids)
def create_message(user,cid,body,parent_id=None):
 ch=get_channel(cid)
 with tx() as c:
  mbody=body; mentions=resolve_mentions(c,body,ch,user['id'])
  cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,user['id'],mbody,parent_id,now_iso())); mid=cur.lastrowid
  c.execute('UPDATE messages SET mentions=? WHERE id=?',(json.dumps(mentions),mid)); row=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  emit_event(c,cid,'message.reply' if parent_id else 'message.created',{'message_id':mid,'body':mbody,'author_id':user['id'],'parent_id':parent_id})
 return row
def list_messages(cid,lim,cursor,replies=False):
 c=connect(); q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id '+('IS NOT NULL' if replies else 'IS NULL')+' ORDER BY id '+('ASC' if replies else 'DESC'); rows=c.execute(q,(cid,)).fetchall();c.close(); rows=rows[(cursor or 0):(cursor or 0)+lim]; return rows,encode_cursor('rep' if replies else 'msg',(cursor or 0)+lim) if len(rows)==lim else None
def reactions_for(mid):
 c=connect(); r=c.execute('SELECT emoji,group_concat(user_id) ids,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY min(rowid)',(mid,)).fetchall();c.close();return [{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in r]
def react(user,mid,emoji,add):
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not m:return None
  if add:
   cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?,?)',(mid,user['id'],emoji,now_iso())); kind='reaction.added'
  else:
   cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji)); kind='reaction.removed'
  if cur.rowcount:
   emit_event(c,m['channel_id'],kind,{'message_id':mid,'emoji':emoji,'user_id':user['id']})
 return reactions_for(mid)
def touch_presence(cid,uid):
 with tx() as c:c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(cid,uid,time.time()))
def drop_presence(cid):
 with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(cid,))
def present_user_ids():
 c=connect();r=[x['user_id'] for x in c.execute('SELECT user_id FROM presence WHERE last_seen>?',(time.time()-3,))];c.close();return set(r)
def create_group(c,wid,handle,name,uids,creator):
 cur=c.execute('INSERT INTO groups_tbl(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(wid,handle,name,creator));gid=cur.lastrowid
 for u in uids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,u))
 return c.execute('SELECT * FROM groups_tbl WHERE id=?',(gid,)).fetchone()
def group_to_json(r):
 c=connect(); ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(r['id'],))];c.close();return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
def update_profile(user,fields):
 allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
 with tx() as c:
  for k,v in fields.items():
   if k in allowed:c.execute(f'UPDATE users SET {k}=? WHERE id=?',(v,user['id']))
  r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone();emit_global(c,{'type':'user.updated','user':user_to_json(r)})
 return r
if __name__=='__main__':
 if '--init-db' in __import__('sys').argv:init_db()
