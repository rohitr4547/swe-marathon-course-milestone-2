import sqlite3,os,secrets,hashlib,re,json,base64,datetime,argparse
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
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));''');c.close()
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,s): return secrets.compare_digest(hash_password(p),s)
def issue_token(c,uid):
 t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));return t
def user_for_token(t):
 if not t:return None
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON u.id=t.user_id WHERE t.token=?',(t,)).fetchone();c.close();return r
def ujson(r):
 return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def wjson(r): return dict(r)
def cjson(r): 
 d=dict(r);d['is_private']=bool(d['is_private']);d['is_dm']=bool(d['is_dm']);d['is_archived']=bool(d['is_archived']);return d
def get_workspace(slug):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def get_channel(i):
 c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def role(w,u):
 c=connect();r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def addmsg(c,ch,u,body,parent=None):
 mentions=[]
 for x in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
  r=c.execute('SELECT u.id FROM users u JOIN members m ON u.id=m.user_id JOIN channels z ON z.workspace_id=m.workspace_id WHERE u.username=? AND z.id=?',(x,ch)).fetchone()
  if r and r[0]!=u and r[0] not in mentions:mentions.append(r[0])
 cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch,u,body,parent,now_iso(),json.dumps(mentions)))
 mid=cur.lastrowid; emit(c,ch,'message.reply' if parent else 'message.created',{'message':mjson(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(),c)});return mid
def mjson(r,db=None):
 own=db is None
 c=db or connect();a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); rc=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]; rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
 if own:c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':ujson(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'mentions':json.loads(r['mentions'] or '[]'),'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs]}
def emit(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0];p={'type':kind,'event_id':n,'channel_id':ch};p.update(payload);c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(p)))
if __name__=='__main__': init_db()

def resolve_mentions(c,body,channel,author_id):
 out=[]
 for name in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
  if name=='channel':
   rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(channel['id'],author_id)).fetchall()
   out.extend(x[0] for x in rows);continue
  row=c.execute('SELECT u.id FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=? AND u.username=? AND u.id!=?',(channel['id'],name,author_id)).fetchone()
  if row:out.append(row[0])
 return list(dict.fromkeys(out))
