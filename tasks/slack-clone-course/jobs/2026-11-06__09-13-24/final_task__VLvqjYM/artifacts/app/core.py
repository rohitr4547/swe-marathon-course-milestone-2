import sqlite3, os, json, secrets, hashlib, base64, re, datetime, time
from contextlib import contextmanager
from zoneinfo import ZoneInfo
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
@contextmanager
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
'''CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id))''',
'''CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0)''',
'''CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id))''',
'''CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0)''',
'''CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id))''',
'''CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,created_at TEXT,PRIMARY KEY(message_id,user_id,emoji))''',
'''CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b))''',
'''CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER)''',
'''CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle))''',
'''CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id))''',
'''CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL)''',
'''CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT)''',
'''CREATE TABLE IF NOT EXISTS message_mentions(message_id INTEGER,user_id INTEGER,PRIMARY KEY(message_id,user_id))''']:
  c.execute(q)
 c.close()
def hash_password(pw):
 s=secrets.token_hex(16); return s+':'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
 try: s,h=stored.split(':'); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
 except: return False
def issue_token(c,user_id):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES (?,?)',(t,user_id)); return t
def user_for_token(token):
 if not token:return None
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON u.id=t.user_id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def get_workspace(slug):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO memberships VALUES (?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES (?,?)',(g['id'],u))
def channel_to_json(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_channel(i):
 c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def create_channel_row(c,w,name,private=False,topic=''):
 c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES (?,?,?,?)',(w,name,int(private),topic)); return c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone()
def add_channel_member(c,ch,u):
 c.execute('INSERT OR IGNORE INTO channel_members VALUES (?,?)',(ch,u))
def remove_channel_member(c,ch,u): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u))
def join_channel(user,ch):
 with tx() as c:
  r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  if not r:return None
  add_channel_member(c,ch,user['id'])
  if r['workspace_id']: add_workspace_member(c,r['workspace_id'],user['id'],'member')
 return get_channel(ch)
def can_read_channel(u,ch):
 if not ch:return False
 if ch['is_dm']:
  c=connect();x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone();c.close();return bool(x)
 return bool(member_role(ch['workspace_id'],u)) and bool(_cm(u,ch['id']))
def _cm(u,ch):
 c=connect();x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u)).fetchone();c.close();return x
def require_writable(ch): return not ch['is_archived']
def emit_event(c,ch,kind,payload):
 c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(ch,)); n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0]
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES (?,?,?)',(ch,n,json.dumps(frame)));return frame
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES (?)',(json.dumps(payload),))
def head_event_id(ch):
 c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return r[0] if r else 0
def events_after(ch,s):
 c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,s)).fetchall();c.close();return [json.loads(x[0]) for x in r]
def globals_after(s):
 c=connect();r=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(s,)).fetchall();c.close();return [(x[0],json.loads(x[1])) for x in r]
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
 try:
  x=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4))); assert x['k']==k and isinstance(x['v'],int);return x['v']
 except: raise ValueError()
def parse_paging(q,k):
 try:
  n=int(q.get('limit',50)); assert 1<=n<=200
  return n,decode_cursor(k,q['cursor']) if q.get('cursor') else None
 except: raise ValueError()
def _author(r):
 c=connect();u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone();c.close();return user_to_json(u)
def reactions_for(mid):
 c=connect();rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall();c.close();out=[]
 for x in rows:
  z=next((a for a in out if a['emoji']==x['emoji']),None)
  if not z:z={'emoji':x['emoji'],'count':0,'user_ids':[]};out.append(z)
  z['count']+=1;z['user_ids'].append(x['user_id'])
 return out
def message_to_json(r):
 d=dict(r);d.pop('deleted',None);d['author']=_author(r);d['parent_id']=r['parent_id'];d['reply_count']=_count_replies(r['id']);c=connect();fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall();c.close();d['files']=[file_to_json(x) for x in fs];d['reactions']=reactions_for(r['id']);d['mentions']=_mentions(r['id']);return d
def _count_replies(i):
 c=connect();n=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(i,)).fetchone()[0];c.close();return n
def _mentions(i):
 c=connect();r=c.execute('SELECT user_id FROM message_mentions WHERE message_id=? ORDER BY rowid',(i,)).fetchall();c.close();return [x[0] for x in r]
def resolve_mentions(c,body,channel,author_id):
 ids=set()
 if '@channel' in body:
  ids.update(x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],)) if x[0]!=author_id)
 if '@here' in body:
  ids.update(x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],)) if x[0]!=author_id and x[0] in present_user_ids())
 for name in re.findall(r'(?<![\\w@])@([A-Za-z0-9_-]+)',body):
  if name in ('channel','here'): continue
  gr=c.execute('SELECT g.id FROM groups_ g WHERE g.workspace_id=? AND g.handle=?',(channel['workspace_id'],name)).fetchone() if channel['workspace_id'] else None
  if gr:
   ids.update(x[0] for x in c.execute('SELECT gm.user_id FROM group_members gm JOIN channel_members cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=?',(gr[0],channel['id'])) if x[0]!=author_id)
   continue
  u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
  if u and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel['id'],u[0])).fetchone() and u[0]!=author_id:ids.add(u[0])
 return list(ids)
def create_message(user,ch,body,parent_id=None,file_ids=None):
 with tx() as c:
  r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  if not r:return None
  ids=file_ids or []
  if not isinstance(ids,list) or any(not isinstance(x,int) for x in ids):raise ValueError()
  for fid in ids:
   f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
   if not f or f['uploader_id']!=user['id'] or f['message_id'] is not None:raise ValueError()
  c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES (?,?,?,?,?)',(ch,user['id'],body,parent_id,now_iso()))
  m=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone()
  for fid in ids:c.execute('UPDATE files SET message_id=? WHERE id=?',(m['id'],fid))
  mentions=resolve_mentions(c,body,r,user['id'])
  c.executemany('INSERT OR IGNORE INTO message_mentions VALUES (?,?)',[(m['id'],x) for x in mentions])
  emit_event(c,ch,'message.reply' if parent_id else 'message.created',{'message':dict(m)})
 return message_to_json(m)
def list_messages(ch,limit,cursor):
 c=connect();q='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0';args=[ch]
 if cursor is not None:q+=' AND id<?';args.append(cursor)
 q+=' ORDER BY id DESC LIMIT ?';args.append(limit+1);rs=c.execute(q,args).fetchall();c.close();return rs[:limit],(encode_cursor('messages',rs[limit-1]['id']) if len(rs)>limit else None)
def list_replies(p,limit,cursor):
 c=connect();q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';args=[p]
 if cursor is not None:q+=' AND id>?';args.append(cursor)
 q+=' ORDER BY id LIMIT ?';args.append(limit+1);rs=c.execute(q,args).fetchall();c.close();return rs[:limit],(encode_cursor('replies',rs[limit-1]['id']) if len(rs)>limit else None)
def react(user,mid,emoji,add=True):
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not m:return None
  if add:
   c.execute('INSERT OR IGNORE INTO reactions VALUES (?,?,?,?)',(mid,user['id'],emoji,now_iso()))
   changed=c.execute('SELECT changes()').fetchone()[0]
   kind='reaction.added'
  else:
   c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji))
   changed=c.execute('SELECT changes()').fetchone()[0]
   kind='reaction.removed'
  if changed:
   emit_event(c,m['channel_id'],kind,{'message_id':mid,'emoji':emoji,'user_id':user['id']})
 return reactions_for(mid)
def edit_message(user,mid,body):
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not m:return None
  c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,now_iso(),mid));m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();emit_event(c,m['channel_id'],'message.updated',{'message':message_to_json(m)})
 return message_to_json(m)
def delete_message(user,mid):
 with tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if m:c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
 return {'deleted':True}
def open_dm(user,other):
 a,b=sorted((user['id'],other))
 with tx() as c:
  x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
  if x:return x[0]
  c.execute('INSERT INTO channels(is_private,is_dm) VALUES (1,1)');ch=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO dms VALUES (?,?,?)',(a,b,ch));add_channel_member(c,ch,a);add_channel_member(c,ch,b);return ch
def save_file(user,fn,ct,data):
 if len(data)>10*1024*1024: raise OverflowError
 with tx() as c:
  c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES (?,?,?,?,?)',(user['id'],fn,ct,len(data),now_iso()))
  i=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  row=c.execute('SELECT * FROM files WHERE id=?',(i,)).fetchone()
 os.makedirs('/app/data/files',exist_ok=True)
 with open(f'/app/data/files/{i}','wb') as f:f.write(data)
 return row
def file_to_json(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def create_group(w,creator,handle,name,user_ids):
 with tx() as c:
  if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w,handle)).fetchone():raise KeyError()
  c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES (?,?,?,?)',(w,handle,name,creator));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  c.execute('DELETE FROM group_members WHERE group_id=?',(gid,))
  for u in user_ids:c.execute('INSERT INTO group_members VALUES (?,?)',(gid,u))
  return c.execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone()
def group_to_json(r):
 c=connect();ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(r['id'],))];c.close();return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
def update_group(w,h,fields):
 with tx() as c:
  r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w,h)).fetchone()
  if not r:return None
  if 'name' in fields:c.execute('UPDATE groups_ SET name=? WHERE id=?',(fields['name'],r['id']))
  if 'user_ids' in fields:c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],));c.executemany('INSERT INTO group_members VALUES (?,?)',[(r['id'],x) for x in fields['user_ids']])
  return c.execute('SELECT * FROM groups_ WHERE id=?',(r['id'],)).fetchone()
def delete_group(w,h):
 with tx() as c:
  r=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(w,h)).fetchone()
  if r:
   c.execute('DELETE FROM group_members WHERE group_id=?',(r[0],))
   c.execute('DELETE FROM groups_ WHERE id=?',(r[0],))
 return {'deleted':True}
def touch_presence(i,u):
 with tx() as c:c.execute('INSERT OR REPLACE INTO presence VALUES (?,?,?)',(i,u,time.time()))
def drop_presence(i):
 with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(i,))
def present_user_ids():
 c=connect();r=[x[0] for x in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))];c.close();return r
def update_profile(user,fields):
 allowed={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512),'timezone':None}
 for k,v in fields.items():
  if k not in allowed or not isinstance(v,str):raise ValueError()
  if allowed[k] and not allowed[k][0]<=len(v)<=allowed[k][1]:raise ValueError()
  if k=='timezone':ZoneInfo(v)
 with tx() as c:
  c.execute('UPDATE users SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),user['id']));r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone();emit_global(c,{'type':'user.updated','user':user_to_json(r)})
 return r
def run_slash(user,ch,body):
 if body.startswith('//'):return ('message',body[1:])
 if body.startswith('/me '):return ('message',body) if body[4:].strip() else None
 if body=='/me' or body.startswith('/me '):return None
 if body.startswith('/shrug'):return ('message',body[6:].strip()+' ¯\\_(ツ)_/¯').strip()
 return ('message',body)
if __name__=='__main__' and '--init-db' in __import__('sys').argv:init_db()
