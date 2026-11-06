import sqlite3,os,secrets,hashlib,re,json,base64,datetime,time
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
class Tx:
 def __enter__(s): s.c=connect();s.c.execute('BEGIN IMMEDIATE');return s.c
 def __exit__(s,t,v,b): s.c.execute('ROLLBACK' if t else 'COMMIT');s.c.close()
def tx(): return Tx()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect();c.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT "[]");
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));''');c.close()
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,s): return secrets.compare_digest(hash_password(p),s)
def issue_token(c,uid):
 t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,uid));return t
def user_for_token(t):
 if not t:return None
 c=connect();r=c.execute('select u.* from users u join tokens t on t.user_id=u.id where t.token=?',(t,)).fetchone();c.close();return r
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return dict(r)
def channel_to_json(r):
 d=dict(r);d['is_private']=bool(d['is_private']);d['is_dm']=bool(d['is_dm']);d['is_archived']=bool(d['is_archived']);return d
def get_workspace(slug):
 c=connect();r=c.execute('select * from workspaces where slug=?',(slug,)).fetchone();c.close();return r
def get_channel(i):
 c=connect();r=c.execute('select * from channels where id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('select role from members where workspace_id=? and user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
 c.execute('insert or ignore into members values(?,?,?)',(w,u,role));g=c.execute("select id from channels where workspace_id=? and name='general'",(w,)).fetchone()
 if g:c.execute('insert or ignore into channel_members values(?,?)',(g['id'],u))
def add_channel_member(c,ch,u):
 c.execute('insert or ignore into channel_members values(?,?)',(ch,u))
def remove_channel_member(c,ch,u): c.execute('delete from channel_members where channel_id=? and user_id=?',(ch,u))
def join_channel(user,ch):
 with tx() as c:
  r=c.execute('select * from channels where id=?',(ch,)).fetchone()
  if not r:return None
  if r['workspace_id'] and not c.execute('select 1 from members where workspace_id=? and user_id=?',(r['workspace_id'],user['id'])).fetchone(): add_workspace_member(c,r['workspace_id'],user['id'])
  add_channel_member(c,ch,user['id']);return dict(r)
def can_read_channel(uid,ch):
 if ch['is_dm']: 
  c=connect();x=c.execute('select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],uid)).fetchone();c.close();return bool(x)
 c=connect();x=c.execute('select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],uid)).fetchone();c.close();return bool(x)
def emit_event(c,ch,kind,payload):
 n=c.execute('update channels set last_event_id=last_event_id+1 where id=? returning last_event_id',(ch,)).fetchone()['last_event_id'];d={'type':kind,'event_id':n,'channel_id':ch};d.update(payload);c.execute('insert into events values(?,?,?)',(ch,n,json.dumps(d)));return d
def head_event_id(ch):
 c=connect();r=c.execute('select last_event_id from channels where id=?',(ch,)).fetchone();c.close();return r['last_event_id'] if r else 0
def events_after(ch,n):
 c=connect();r=c.execute('select payload from events where channel_id=? and event_id>? order by event_id',(ch,n)).fetchall();c.close();return [json.loads(x['payload']) for x in r]
def create_channel_row(c,w,name,private=False,topic=''):
 cur=c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w,name,int(private),topic));return c.execute('select * from channels where id=?',(cur.lastrowid,)).fetchone()
def resolve_mentions(c,body,ch,author):
 out=[]
 if '@channel' in body:
  out += [x['user_id'] for x in c.execute('select user_id from channel_members where channel_id=? and user_id!=?',(ch['id'],author))]
 for m in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
  r=c.execute('select u.id from users u join members m on m.user_id=u.id where m.workspace_id=? and u.username=?',(ch['workspace_id'],m)).fetchone()
  if r and r['id']!=author:out.append(r['id'])
 return list(dict.fromkeys(out))
def msg_json(r):
 c=connect();a=c.execute('select * from users where id=?',(r['author_id'],)).fetchone();rs=c.execute('select emoji,count(*) n,group_concat(user_id) ids from reactions where message_id=? group by emoji',(r['id'],)).fetchall();reps=c.execute('select count(*) n from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()['n'];c.close()
 d=dict(r);d['author']=user_to_json(a);d['parent_id']=d['parent_id'];d['reply_count']=reps;d['reactions']=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rs];d['mentions']=json.loads(d['mentions'] or '[]');d['files']=[];return d
def create_message(user,ch,body,parent=None):
 with tx() as c:
  r=c.execute('select * from channels where id=?',(ch,)).fetchone();ms=resolve_mentions(c,body,r,user['id']);cur=c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at,mentions) values(?,?,?,?,?,?)',(ch,user['id'],body,parent,now_iso(),json.dumps(ms)));m=c.execute('select * from messages where id=?',(cur.lastrowid,)).fetchone();emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':msg_json(m)});return msg_json(m)
def list_messages(ch, replies=False):
 c=connect();q='select * from messages where channel_id=? and deleted=0 and '+('parent_id is not null' if replies else 'parent_id is null')+' order by id '+('asc' if replies else 'desc');rs=c.execute(q,(ch,)).fetchall();c.close();return [msg_json(x) for x in rs]
def edit_message(user,mid,body):
 with tx() as c:
  r=c.execute('select * from messages where id=?',(mid,)).fetchone()
  if not r:return ('404',None)
  if r['deleted']:return ('409',None)
  if r['author_id']!=user['id']:return ('403',None)
  ch=c.execute('select * from channels where id=?',(r['channel_id'],)).fetchone();ms=resolve_mentions(c,body,ch,user['id']);c.execute('update messages set body=?,edited_at=?,mentions=? where id=?',(body,now_iso(),json.dumps(ms),mid));m=c.execute('select * from messages where id=?',(mid,)).fetchone();d=msg_json(m);emit_event(c,ch['id'],'message.updated',{'message':d});return ('200',d)
def delete_message(user,mid):
 with tx() as c:
  r=c.execute('select * from messages where id=?',(mid,)).fetchone()
  if not r:return '404'
  ch=c.execute('select * from channels where id=?',(r['channel_id'],)).fetchone();role=member_role(ch['workspace_id'],user['id']) if ch['workspace_id'] else None
  if r['author_id']!=user['id'] and role!='owner':return '403'
  if not r['deleted']:
   c.execute('update messages set deleted=1 where id=?',(mid,))
   emit_event(c,ch['id'],'message.deleted',{'message_id':mid})
  return '200'
def open_dm(user,other):
 a,b=sorted((user['id'],other))
 with tx() as c:
  r=c.execute('select channel_id from dms where user_a=? and user_b=?',(a,b)).fetchone()
  if r:return r['channel_id']
  cur=c.execute('insert into channels(is_private,is_dm) values(1,1)');ch=cur.lastrowid;c.execute('insert into dms values(?,?,?)',(a,b,ch));c.execute('insert into channel_members values(?,?),(?,?)',(ch,a,ch,b));return ch
if __name__=='__main__': init_db()
