import sqlite3,os,secrets,hashlib,base64,json,re
from datetime import datetime,timezone
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
class tx:
 def __enter__(self): self.c=connect();self.c.execute('BEGIN IMMEDIATE');return self.c
 def __exit__(self,t,v,b):
  self.c.execute('ROLLBACK' if t else 'COMMIT');self.c.close()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 os.makedirs('/app/data',exist_ok=True);c=connect();c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT "[]");
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires REAL,max_uses INTEGER,uses INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);''');c.close()
def hash_password(pw):
 s=secrets.token_hex(16);return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
 try:s,h=stored.split('$');return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
 except:return False
def issue_token(c,uid):
 t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));return t
def user_for_token(t):
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON u.id=t.user_id WHERE t.token=?',(t,)).fetchone();c.close();return r
def emit_global(c,payload):
 c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return dict(r)
def channel_to_json(r):
 d=dict(r);d['is_private']=bool(d['is_private']);d['is_dm']=bool(d['is_dm']);d['is_archived']=bool(d['is_archived']);return d
def get_workspace(slug):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def get_channel(i):
 c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w,u,role))
 g=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(w,)).fetchone()
 if g:add_channel_member(c,g['id'],u)
def add_channel_member(c,ch,u):
 old=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u)).fetchone()
 c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
 if not old: emit_event(c,ch,'member.joined',{'user_id':u})
def remove_channel_member(c,ch,u):
 old=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u)).fetchone()
 c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u))
 if old: emit_event(c,ch,'member.left',{'user_id':u})
def can_read_channel(u,ch):
 if ch['workspace_id'] is None:return True
 return bool(member_role(ch['workspace_id'],u) and (not ch['is_private'] or _cm(ch['id'],u)))
def _cm(ch,u):
 c=connect();x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u)).fetchone();c.close();return x
def emit_event(c,ch,kind,payload):
 c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(ch,));n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0]
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame)));return frame
def head_event_id(ch):
 c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return r[0] if r else 0
def events_after(ch,n):
 c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,n));a=[json.loads(x[0]) for x in r];c.close();return a
def group_to_json(r):
 return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'] or '[]')}
def create_group(w,u,handle,name,ids):
 with tx() as c:
  cur=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w,handle,name,u,json.dumps(ids)));return group_to_json(c.execute('SELECT * FROM groups_ WHERE id=?',(cur.lastrowid,)).fetchone())

def resolve_mentions(c,body,channel,author_id):
 ids=[]
 for name in re.findall(r'(?<![\w@.])@([A-Za-z0-9_]+)(?![\w@.]|@)',body):
  if name.startswith('group-') or c.execute('SELECT 1 FROM groups_ WHERE handle=? AND workspace_id=?',(name,channel['workspace_id'])).fetchone():
   handle=name[6:] if name.startswith('group-') else name
   g=c.execute('SELECT user_ids FROM groups_ WHERE lower(handle)=lower(?) AND workspace_id=?',(handle,channel['workspace_id'])).fetchone()
   if g: ids += [x for x in json.loads(g[0]) if x!=author_id and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel['id'],x)).fetchone()]
   continue
  if name=='here':
   rows=c.execute("SELECT DISTINCT cm.user_id FROM channel_members cm JOIN presence p ON p.user_id=cm.user_id WHERE cm.channel_id=? AND p.last_seen>strftime('%s','now')-3 AND cm.user_id!=?",(channel['id'],author_id)).fetchall();ids += [x[0] for x in rows]; continue
  if name=='channel':
   rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(channel['id'],author_id)).fetchall(); ids += [x[0] for x in rows]; continue
  row=c.execute('SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE lower(u.username)=lower(?) AND m.workspace_id=?',(name,channel['workspace_id'] or -1)).fetchone()
  if row and row[0]!=author_id and (not channel['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel['id'],row[0])).fetchone()): ids.append(row[0])
 return list(dict.fromkeys(ids))
def create_message(u,ch,body,parent_id=None):
 with tx() as c:
  channel=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  mentions=resolve_mentions(c,body,channel,u['id'])
  r=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,u['id'],body,parent_id,now_iso()));mid=r.lastrowid
  c.execute('UPDATE messages SET mentions=? WHERE id=?',(json.dumps(mentions),mid));m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();emit_event(c,ch,'message.reply' if parent_id else 'message.created',{'message':message_to_json(m)});return message_to_json(m)
def reactions_for(mid):
 c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall(); c.close()
 out=[]
 for x in rows:
  z=next((q for q in out if q['emoji']==x['emoji']),None)
  if not z:z={'emoji':x['emoji'],'count':0,'user_ids':[]};out.append(z)
  z['count']+=1;z['user_ids'].append(x['user_id'])
 return out
def react(user,message_id,emoji,add=True):
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(message_id,)).fetchone()
  if not m:return None
  ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
  if add:
   old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user['id'],emoji)).fetchone()
   if not old:
    c.execute('INSERT INTO reactions VALUES(?,?,?)',(message_id,user['id'],emoji))
    emit_event(c,ch['id'],'reaction.added',{'message_id':message_id,'emoji':emoji,'user_id':user['id']})
  else:
   old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user['id'],emoji)).fetchone()
   if old:
    c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user['id'],emoji))
    emit_event(c,ch['id'],'reaction.removed',{'message_id':message_id,'emoji':emoji,'user_id':user['id']})
 return reactions_for(message_id)
def edit_message(user,message_id,body):
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
  if not m:return ('404',None)
  if m['deleted']:return ('409',None)
  if m['author_id']!=user['id']:return ('403',None)
  ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone();mentions=resolve_mentions(c,body,ch,user['id']);ts=now_iso()
  c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(body,ts,json.dumps(mentions),message_id))
  x=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone();emit_event(c,ch['id'],'message.updated',{'message':message_to_json(x)})
  return ('200',message_to_json(x))
def delete_message(user,message_id):
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
  if not m:return ('404',None)
  if m['deleted']:return ('200',{'deleted':True})
  ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
  if m['author_id']!=user['id']:
   w=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
   if not w or w['owner_id']!=user['id']:return ('403',None)
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(message_id,));c.execute('DELETE FROM reactions WHERE message_id=?',(message_id,))
  emit_event(c,ch['id'],'message.deleted',{'message_id':message_id});return ('200',{'deleted':True})

def message_to_json(r):
 c=connect(); au=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(au) if au else None,'body':r['body'],'parent_id':r['parent_id'],'reply_count':0,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[dict(x) for x in connect().execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()],'reactions':reactions_for(r['id']),'mentions':json.loads(r['mentions'] or '[]')}
def list_messages(ch,parent=None):
 c=connect();q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND '+('parent_id=?' if parent else 'parent_id IS NULL')+' ORDER BY id DESC';rows=c.execute(q,(ch,parent) if parent else (ch,)).fetchall();out=[message_to_json(x) for x in rows]
 for m in out:m['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(m['id'],)).fetchone()[0]
 c.close();return out
if __name__=='__main__' and '--init-db' in __import__('sys').argv:init_db()
