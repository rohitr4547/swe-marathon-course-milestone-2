import sqlite3,os,json,secrets,hashlib,datetime,contextlib,re,sys
DB='/app/data/huddle.db'
def connect():
 os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
@contextlib.contextmanager
def tx():
 c=connect()
 try:c.execute('BEGIN IMMEDIATE');yield c;c.execute('COMMIT')
 except: c.execute('ROLLBACK');raise
 finally:c.close()
def now_iso():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect();c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at REAL,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);CREATE TABLE IF NOT EXISTS presence_channels(conn_id TEXT,channel_id INTEGER,PRIMARY KEY(conn_id,channel_id));CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);''');c.close()
def hash_password(p):return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,s):return hash_password(p)==s
def issue_token(c,u):
 t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u));return t
def user_for_token(t):
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone();c.close();return r
def user_to_json(r):return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def workspace_to_json(r):return dict(r)
def channel_to_json(r):return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def get_workspace(s):c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(s,)).fetchone();c.close();return r
def get_channel(i):c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):c=connect();r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def emit_event(c,ch,k,p):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id'];x={'type':k,'event_id':n,'channel_id':ch};x.update(p);c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(x)));return x
def add_channel_member(c,ch,u):
 if c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u)).fetchone(): return False
 c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u))
 try: emit_event(c,ch,'member.joined',{'user_id':u})
 except sqlite3.OperationalError: pass
 return True
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role));g=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(w,)).fetchone()
 if g:add_channel_member(c,g['id'],u)
def open_dm(u,other_id):
 a,b=sorted((u['id'],other_id))
 with tx() as c:
  x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
  if x:return x['channel_id']
  c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',('',1));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,b))
 return cid
def save_file(u,filename,content_type,data):
 os.makedirs('/app/data/files',exist_ok=True)
 with tx() as c:
  c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],filename,content_type,len(data),now_iso()));fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
 open('/app/data/files/'+str(fid),'wb').write(data)
 return fid
def file_to_json(r):
 return {'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}
def create_channel_row(c,w,n,private=False,topic=''):
 return c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,n,int(private),topic)).lastrowid
def _reply_count(r):
 c=connect();n=c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n'];c.close();return n
def reactions_for(mid):
 c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall();c.close()
 out=[]
 for x in rows:
  q=next((z for z in out if z['emoji']==x['emoji']),None)
  if not q:q={'emoji':x['emoji'],'count':0,'user_ids':[]};out.append(q)
  q['count']+=1;q['user_ids'].append(x['user_id'])
 return out
def file_to_json(r): return {'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}
def message_json(r):
 c=connect();a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone();rr=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall();ff=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall();c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':_reply_count(r),'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[file_to_json(x) for x in ff],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rr],'mentions':json.loads(r['mentions'] or '[]')}
def react(u,mid,emoji,add=True):
 result=[]
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
  if not m:return None
  ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
  if add:
   cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
   if cur.rowcount:emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
  else:
   cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
   if cur.rowcount:emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
  rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall()
  for x in rows:
   q=next((z for z in result if z['emoji']==x['emoji']),None)
   if not q:q={'emoji':x['emoji'],'count':0,'user_ids':[]};result.append(q)
   q['count']+=1;q['user_ids'].append(x['user_id'])
 return result

def create_message(u,ch,body,parent=None):
 with tx() as c:
  ids=[]
  for name in re.findall(r'(?<![\w@.])@(\w+)',body):
   if name=='channel':
    rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch,u['id'])).fetchall()
    ids.extend(x['user_id'] for x in rows)
   elif name=='here':
    rows=c.execute('SELECT DISTINCT p.user_id FROM presence p JOIN channel_members cm ON cm.user_id=p.user_id WHERE cm.channel_id=? AND p.user_id!=? AND p.last_seen>?',(ch,u['id'],__import__('time').time()-3)).fetchall()
    ids.extend(x['user_id'] for x in rows)
   else:
    x=c.execute('SELECT u.id FROM users u JOIN members m ON m.user_id=u.id JOIN channels z ON z.workspace_id=m.workspace_id WHERE u.username=? AND z.id=? AND u.id!=?',(name,ch,u['id'])).fetchone()
    if x: ids.append(x['id'])
    else:
     gs=c.execute('SELECT g.id FROM groups g JOIN channels z ON z.workspace_id=g.workspace_id WHERE g.handle=? AND z.id=?',(name,ch)).fetchall()
     for g in gs:
      rows=c.execute('SELECT gm.user_id FROM group_members gm JOIN channel_members cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=? AND gm.user_id!=?',(g['id'],ch,u['id'])).fetchall()
      ids.extend(x['user_id'] for x in rows)
  ids=list(dict.fromkeys(ids));mid=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch,u['id'],body,parent,now_iso(),json.dumps(ids))).lastrowid;row=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':message_json(row)})
 return message_json(row)
if __name__=='__main__' and '--init-db' in sys.argv:init_db()
