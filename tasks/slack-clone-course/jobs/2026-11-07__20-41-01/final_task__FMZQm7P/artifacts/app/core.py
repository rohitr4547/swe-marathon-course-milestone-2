import sqlite3,os,sys,json,hashlib,secrets,re,base64,time
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
DB='/app/data/huddle.db'
def connect():
 os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
class _Tx:
 def __enter__(s): s.c=connect();s.c.execute('BEGIN IMMEDIATE');return s.c
 def __exit__(s,t,v,b): s.c.execute('ROLLBACK' if t else 'COMMIT');s.c.close()
def tx(): return _Tx()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,x): return secrets.compare_digest(hash_password(p),x)
def issue_token(c,u): t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u));return t
def user_for_token(t):
 if not t:return None
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone();c.close();return r
def user_to_json(r): return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def init_db():
 c=connect();c.execute('PRAGMA journal_mode=WAL');c.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS memberships(user_id INTEGER,channel_id INTEGER,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS workspace_members(user_id INTEGER,workspace_id INTEGER,role TEXT,PRIMARY KEY(user_id,workspace_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,mentions TEXT DEFAULT '[]',deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);''');c.close()
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def get_workspace(s):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(s,)).fetchone();c.close();return r
def get_channel(i):
 c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_channel_member(c,u,ch): c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?)',(u,ch))
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(u,w,role));r=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if r:add_channel_member(c,u,r['id'])
def emit_event(c,ch,kind,payload):
 c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(ch,));n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0];p={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(p)));return p
def emit_global(c,p): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(p),))
def message_to_json(r):
 c=connect();a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone();rc=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall();files=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall();replies=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0];c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[file_to_json(x) for x in files],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rc],'mentions':json.loads(r['mentions'] or '[]')}
def file_to_json(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def resolve_mentions(c,body,ch,author):
 out=set();w=ch['workspace_id']
 for x in re.findall(r'(?<!\w)@([A-Za-z0-9_-]+)',body):
  if x=='channel': out.update(r[0] for r in c.execute('SELECT user_id FROM memberships WHERE channel_id=?',(ch['id'],)))
  elif x=='here': out.update(r[0] for r in c.execute('SELECT user_id FROM memberships WHERE channel_id=? AND user_id IN (SELECT user_id FROM presence WHERE last_seen>?)',(ch['id'],time.time()-3)))
  else:
   g=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(w,x)).fetchone() if w else None
   if g: out.update(z[0] for z in c.execute('SELECT user_id FROM group_members WHERE group_id=? AND user_id IN (SELECT user_id FROM memberships WHERE channel_id=?)',(g[0],ch['id'])))
   u=c.execute('SELECT id FROM users WHERE username=?',(x,)).fetchone()
   if u and c.execute('SELECT 1 FROM memberships WHERE channel_id=? AND user_id=?',(ch['id'],u[0])).fetchone():out.add(u[0])
   g=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(w,x)).fetchone() if w else None
   if g: out.update(z[0] for z in c.execute('SELECT user_id FROM group_members WHERE group_id=? AND user_id IN (SELECT user_id FROM memberships WHERE channel_id=?)',(g[0],ch['id'])))
 out.discard(author);return sorted(out)
def create_message(u,ch,body,parent=None,file_ids=None):
 with tx() as c:
  r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone();m=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,u['id'],body,parent,now_iso()));mid=m.lastrowid
  mentions=resolve_mentions(c,body,r,u['id'])
  c.execute('UPDATE messages SET mentions=? WHERE id=?',(json.dumps(mentions),mid))
  for fid in (file_ids or []): c.execute('UPDATE files SET message_id=? WHERE id=?',(mid,fid))
  row=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();kind='message.reply' if parent else 'message.created';emit_event(c,ch,kind,{'message':{'id':mid,'channel_id':ch,'author_id':u['id'],'body':body,'parent_id':parent,'created_at':row['created_at'],'edited_at':None,'reply_count':0,'files':[],'reactions':[],'mentions':mentions}})
 return message_to_json(row)
def react(u,mid,e,add=True):
 with tx() as c:
  r=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
  if not r:return None
  if not c.execute('SELECT 1 FROM memberships WHERE user_id=? AND channel_id=?',(u['id'],r['channel_id'])).fetchone(): raise PermissionError()
  exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],e)).fetchone()
  changed=False
  if add and not exists:c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],e));changed=True
  if not add and exists:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],e));changed=True
  if changed: emit_event(c,r['channel_id'],'reaction.added' if add else 'reaction.removed',{'message_id':mid,'user_id':u['id'],'emoji':e})
  x=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(emoji)',(mid,)).fetchall()
  return [{'emoji':z['emoji'],'count':z['n'],'user_ids':[int(i) for i in z['ids'].split(',')]} for z in x]

def reactions_for(mid):
 c=connect();x=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall();c.close();return [{'emoji':r['emoji'],'count':r['n'],'user_ids':[int(i) for i in r['ids'].split(',')]} for r in x]
def save_file(u,name,ct,data):
 with tx() as c:
  q=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],name,ct,len(data),now_iso()));i=q.lastrowid;os.makedirs('/app/data/files',exist_ok=True);open(f'/app/data/files/{i}','wb').write(data);return c.execute('SELECT * FROM files WHERE id=?',(i,)).fetchone()
def touch_presence(i,u): connect().execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(i,u,time.time()))
def drop_presence(i): connect().execute('DELETE FROM presence WHERE conn_id=?',(i,))
def present_user_ids(): return [r[0] for r in connect().execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))]
if __name__=='__main__' and '--init-db' in sys.argv:init_db()
def update_profile(user,fields):
 allowed={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512),'timezone':(1,128)}
 for k,v in fields.items():
  if k not in allowed or not isinstance(v,str) or not allowed[k][0]<=len(v)<=allowed[k][1]: raise ValueError()
  if k=='timezone': ZoneInfo(v)
 with tx() as c:
  for k,v in fields.items():c.execute(f'UPDATE users SET {k}=? WHERE id=?',(v,user['id']))
  r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone();emit_global(c,{'type':'user.updated','user':user_to_json(r)});return r
def create_group(w,creator,handle,name,ids):
 if not re.fullmatch(r'[a-z0-9-]{1,32}',handle):raise ValueError()
 with tx() as c:
  if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w,handle)).fetchone():raise FileExistsError()
  for i in ids:
   if not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,i)).fetchone():raise ValueError()
  q=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w,handle,name,creator));gid=q.lastrowid
  for i in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,i))
  return c.execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone()
def group_to_json(r):
 c=connect();ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id',(r['id'],))];c.close();return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
def open_dm(u,o):
 a,b=sorted((u['id'],o))
 with tx() as c:
  r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
  if r:return r[0]
  q=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)');ch=q.lastrowid;c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,ch));add_channel_member(c,a,ch);add_channel_member(c,b,ch);return ch
