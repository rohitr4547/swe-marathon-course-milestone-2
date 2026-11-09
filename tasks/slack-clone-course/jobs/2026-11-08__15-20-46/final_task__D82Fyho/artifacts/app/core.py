import sqlite3, os, hashlib, secrets, json, re, base64, datetime, contextlib, argparse
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
@contextlib.contextmanager
def tx():
    c=connect()
    try: c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
    except: c.execute('ROLLBACK'); raise
    finally: c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 for q in [
 '''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '')''',
 '''CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER)''',
 '''CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open')''',
 '''CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0)''',
 '''CREATE TABLE IF NOT EXISTS members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id))''',
 '''CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id))''',
 '''CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0)''',
 '''CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji))''',
 '''CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id))''',
 '''CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT)''',
 '''CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER)''',
 '''CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER)''',
 '''CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id))''',
 '''CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL)''',
 '''CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b))''']:
  c.execute(q)
 c.close()
def hash_password(pw): return hashlib.sha256(pw.encode()).hexdigest()
def verify_password(pw,stored): return secrets.compare_digest(hash_password(pw),stored)
def issue_token(c,user_id):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,user_id)); return t
def user_for_token(token):
 if not token:return None
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
 return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def workspace_to_json(r): return dict(r)
def channel_to_json(r):
 d=dict(r)
 for k in ('is_private','is_dm','is_archived'): d[k]=bool(d.get(k))
 return d
def get_workspace(slug):
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
 c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
 c=connect(); r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(ch):
 c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r[0] if r else 0
def events_after(ch,s):
 c=connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,s)).fetchall(); c.close(); return [json.loads(x[0]) for x in rows]
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO members VALUES(?,?)',(ch,u))
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:add_channel_member(c,g[0],u)
def create_channel_row(c,w,name,private=0,topic=''):
 return c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?) RETURNING *',(w,name,int(private),topic)).fetchone()
def create_message(user,ch,body,parent=None):
 with tx() as c:
  r=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?) RETURNING *',(ch,user['id'],body,parent,now_iso())).fetchone()
  emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':message_to_json(r)})
 return r
def message_to_json(r):
 d=dict(r); d['author']=user_to_json(user_for_id(r['author_id'])); d['parent_id']=r['parent_id']; d['reply_count']=0; d['files']=[]; d['reactions']=[]; d['mentions']=[]; return d
def user_for_id(i):
 c=connect(); r=c.execute('SELECT * FROM users WHERE id=?',(i,)).fetchone(); c.close(); return r
if __name__=='__main__':
 if '--init-db' in __import__('sys').argv:init_db()
def reactions_for(message_id,c=None):
 own=c is None
 if c is None:c=connect()
 rows=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY min(rowid)',(message_id,)).fetchall()
 if own:c.close()
 return [{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rows]
def open_dm(user, other_id):
 with tx() as c:
  if not c.execute('SELECT id FROM users WHERE id=?',(other_id,)).fetchone(): return None
  a,b=sorted((user['id'],int(other_id)))
  r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
  if r:return r[0]
  ch=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1) RETURNING id',( )).fetchone()[0]
  c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,ch));add_channel_member(c,ch,user['id']);add_channel_member(c,ch,int(other_id))
  return ch
def save_file(user,filename,content_type,data):
 with tx() as c:
  r=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?) RETURNING *',(user['id'],filename,content_type,len(data),now_iso())).fetchone()
  os.makedirs('/app/data/files',exist_ok=True)
  open('/app/data/files/'+str(r['id']),'wb').write(data)
 return r
def file_to_json(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def run_slash(user, channel, body):
 if body.startswith('//'): return {'body':body[1:]}
 if not body.startswith('/'): return {'body':body}
 parts=body.split(None,1); cmd=parts[0].lower(); text=parts[1] if len(parts)>1 else ''
 if cmd=='/me':
  if not text.strip(): return {'error':'text required'}
  return {'body':body}
 if cmd=='/shrug': return {'body':(text+' ' if text else '')+'¯\\_(ツ)_/¯'}
 if cmd=='/topic':
  if not text.strip(): return {'error':'topic required'}
  with tx() as c:
   c.execute('UPDATE channels SET topic=? WHERE id=?',(text,channel['id']))
   ch=c.execute('SELECT * FROM channels WHERE id=?',(channel['id'],)).fetchone()
   emit_event(c,channel['id'],'channel.updated',{'channel':channel_to_json(ch)})
  return {'side':'channel','channel':ch}
 if cmd=='/invite':
  m=re.fullmatch(r'@([A-Za-z0-9_]+)',text.strip())
  if not m:return {'error':'invalid invite'}
  c=connect(); target=c.execute('SELECT * FROM users WHERE username=?',(m.group(1),)).fetchone();c.close()
  if not target:return {'error':'user not found'}
  with tx() as c:
   add_channel_member(c,channel['id'],target['id'])
   emit_event(c,channel['id'],'member.joined',{'user_id':target['id']})
  return {'side':'invited_user','invited_user':user_to_json(target)}
 if cmd in ('/archive','/unarchive'):
  if not channel['workspace_id'] or member_role(channel['workspace_id'],user['id'])!='owner':return {'forbidden':True}
  val=1 if cmd=='/archive' else 0
  with tx() as c:
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,channel['id']))
   ch=c.execute('SELECT * FROM channels WHERE id=?',(channel['id'],)).fetchone()
   emit_event(c,channel['id'],'channel.archived' if val else 'channel.unarchived',{'channel':channel_to_json(ch)})
  return {'side':'channel','channel':ch}
 return {'error':'unknown command'}
def group_to_json(r,c=None):
 own=c is None
 if c is None:c=connect()
 ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id',(r['id'],)).fetchall()]
 if own:c.close()
 return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
def create_group(workspace_id,creator_id,handle,name,user_ids):
 with tx() as c:
  c.execute('DELETE FROM group_members WHERE group_id NOT IN (SELECT id FROM groups)')
  if c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(workspace_id,handle)).fetchone(): raise ValueError('duplicate')
  valid={x[0] for x in c.execute('SELECT user_id FROM workspace_members WHERE workspace_id=?',(workspace_id,)).fetchall()}
  if not set(user_ids)<=valid: raise ValueError('members')
  r=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?) RETURNING *',(workspace_id,handle,name,creator_id)).fetchone()
  for uid in user_ids:c.execute('INSERT INTO group_members VALUES(?,?)',(r['id'],uid))
  return r
def update_group(gid,name=None,user_ids=None):
 with tx() as c:
  if name is not None:c.execute('UPDATE groups SET name=? WHERE id=?',(name,gid))
  if user_ids is not None:
   c.execute('DELETE FROM group_members WHERE group_id=?',(gid,))
   for uid in user_ids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,uid))
  return c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone()
def delete_group(gid):
 with tx() as c:
  c.execute('DELETE FROM group_members WHERE group_id=?',(gid,))
  c.execute('DELETE FROM groups WHERE id=?',(gid,))
def touch_presence(conn_id,user_id):
 with tx() as c:c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,strftime("%s","now"))',(conn_id,user_id))
def drop_presence(conn_id):
 with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
def update_profile(user, fields):
 allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
 vals={k:v for k,v in fields.items() if k in allowed}
 if any(not isinstance(v,str) for v in vals.values()): raise ValueError('types')
 if 'display_name' in vals and not 1<=len(vals['display_name'])<=80: raise ValueError('length')
 if 'status_text' in vals and len(vals['status_text'])>140: raise ValueError('length')
 if 'status_emoji' in vals and len(vals['status_emoji'])>32: raise ValueError('length')
 if 'avatar_url' in vals and len(vals['avatar_url'])>512: raise ValueError('length')
 if 'timezone' in vals:
  try:
   from zoneinfo import ZoneInfo
   ZoneInfo(vals['timezone'])
  except: raise ValueError('timezone')
 with tx() as c:
  if vals:
   sets=', '.join(k+'=?' for k in vals)
   c.execute('UPDATE users SET '+sets+' WHERE id=?',(*vals.values(),user['id']))
  r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
  emit_global(c,{'type':'user.updated','user':user_to_json(r)})
 return r
def globals_after(since):
 c=connect(); rows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall();c.close()
 return [(x['id'],json.loads(x['payload'])) for x in rows]

def present_user_ids():
 c=connect();r=[x[0] for x in c.execute("SELECT DISTINCT user_id FROM presence WHERE last_seen>strftime('%s','now')-3").fetchall()];c.close();return r
def resolve_mentions(c, body, channel, author_id):
 ids=[]
 if re.search(r'(?<!\w)@channel(?!\w)',body):
  ids += [x[0] for x in c.execute('SELECT user_id FROM members WHERE channel_id=? AND user_id<>?',(channel['id'],author_id)).fetchall()]
 if re.search(r'(?<!\w)@here(?!\w)',body):
  present=set(present_user_ids())
  ids += [x[0] for x in c.execute('SELECT user_id FROM members WHERE channel_id=? AND user_id<>?',(channel['id'],author_id)).fetchall() if x[0] in present]
 for name in re.findall(r'(?<!\w)@([A-Za-z0-9_][A-Za-z0-9_-]*)(?!\w)',body):
  if name in ('channel','here'): continue
  u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
  if u and c.execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(channel['id'],u[0])).fetchone():ids.append(u[0])
  g=c.execute('SELECT id FROM groups WHERE workspace_id=? AND handle=?',(channel['workspace_id'],name)).fetchone() if channel['workspace_id'] else None
  if g:
   ids += [x[0] for x in c.execute('SELECT gm.user_id FROM group_members gm JOIN members m ON m.user_id=gm.user_id WHERE gm.group_id=? AND m.channel_id=?',(g[0],channel['id'])).fetchall()]
 return list(dict.fromkeys(x for x in ids if x!=author_id))
def edit_message(user,message_id,body):
 if not isinstance(body,str) or not body.strip(): raise ValueError('body')
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
  if not m: return None
  if m['deleted']: raise RuntimeError('deleted')
  if m['author_id']!=user['id']: raise PermissionError()
  c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,now_iso(),message_id))
  m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
  emit_event(c,m['channel_id'],'message.updated',{'message':message_to_json(m)})
  return m
def delete_message(user,message_id):
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
  if not m:return None
  if m['deleted']:return m
  ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
  role=member_role(ch['workspace_id'],user['id']) if ch['workspace_id'] else None
  if m['author_id']!=user['id'] and role!='owner':raise PermissionError()
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(message_id,))
  emit_event(c,ch['id'],'message.deleted',{'message_id':message_id})
  return c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
