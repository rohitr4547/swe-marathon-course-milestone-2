import os,sqlite3,secrets,hashlib,re,datetime,json,sys,fcntl
from flask import Flask,request,jsonify,send_file,Response
from werkzeug.utils import secure_filename
DB='/app/data/huddle.db'; os.makedirs('/app/data',exist_ok=True); EVENT_LOCK=open('/app/data/events.lock','a+')
node=int(os.environ.get('NODE_ID','0')); app=Flask(__name__); OPEN_USERS=set()
def db():
 c=sqlite3.connect(DB,timeout=30,check_same_thread=False,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=30000'); return c
def init():
 c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,UNIQUE(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,UNIQUE(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,event_id INTEGER);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,UNIQUE(message_id,user_id,emoji)); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,path TEXT); CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER UNIQUE); CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle)); CREATE TABLE IF NOT EXISTS presence(user_id INTEGER PRIMARY KEY,updated_at TEXT); CREATE TABLE IF NOT EXISTS profile_events(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,payload TEXT); CREATE TABLE IF NOT EXISTS group_users(group_id INTEGER,user_id INTEGER,UNIQUE(group_id,user_id));
CREATE TABLE IF NOT EXISTS dms(a INTEGER,b INTEGER,channel_id INTEGER,UNIQUE(a,b));
'''); c.commit(); c.close()
init()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
def auth():
 t=request.headers.get('Authorization','').replace('Bearer ',''); r=db().execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(t,)).fetchone()
 return r
def userobj(r):
 if not r:return None
 d=dict(r); d.pop('password',None); return d
def err(x,s): return jsonify({'error':x}),s
def msgobj(r):
 d=dict(r); d['author']=userobj(db().execute('SELECT * FROM users WHERE id=?',(d['author_id'],)).fetchone()); d['parent_id']=d['parent_id']; d['reply_count']=db().execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(d['id'],)).fetchone()['n']; d['files']=[dict(x) for x in db().execute('SELECT f.id,f.uploader_id,f.filename,f.content_type,f.size,f.created_at FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(d['id'],))]
 d['mentions']=[]
 c=db(); members={x['user_id'] for x in c.execute('SELECT user_id FROM cmembers WHERE channel_id=?',(d['channel_id'],))}
 if d['body'].startswith('/'): pass
 else:
  for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_:-]+)',d['body']):
   if name=='channel': ids=members
   elif name=='here': ids=members & {x['user_id'] for x in c.execute('SELECT user_id FROM presence')}
   else:
    g=c.execute('SELECT id FROM groups WHERE workspace_id=(SELECT workspace_id FROM channels WHERE id=?) AND handle=?',(d['channel_id'],name)).fetchone()
    if g: ids={x['user_id'] for x in c.execute('SELECT user_id FROM group_users WHERE group_id=?',(g['id'],))}
    else:
     u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone(); ids={u['id']} if u else set()
   d['mentions'] += [x for x in ids if x in members and x!=d['author_id'] and x not in d['mentions']]
 d['reactions']=[]
 for x in db().execute('SELECT emoji,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(d['id'],)): d['reactions'].append({'emoji':x['emoji'],'count':x['n'],'user_ids':[z['user_id'] for z in db().execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=?',(d['id'],x['emoji']))]})
 return d
@app.before_request
def websocket_auth_guard():
 if request.path=='/api/ws':
  token=request.args.get('token','').replace('Bearer ','')
  if not db().execute('SELECT 1 FROM tokens WHERE token=?',(token,)).fetchone():
   return err('unauthorized',401)

try:
 from flask_sock import Sock
 sock=Sock(app)
 @sock.route('/api/ws')
 def websocket(ws):
  token=request.args.get('token','').replace('Bearer ','')
  uidrow=db().execute('SELECT user_id FROM tokens WHERE token=?',(token,)).fetchone()
  if not uidrow:
   ws.close(); return
  OPEN_USERS.add(uidrow['user_id'])
  db().execute('INSERT OR REPLACE INTO presence(user_id,updated_at) VALUES(?,?)',(uidrow['user_id'],now()))
  state={'channels':set(),'last':0,'last_by_channel':{},'alive':True,'paused':True}
  import threading,time
  def pump():
   while state['alive']:
    try:
     if state['paused']:
      time.sleep(.05); continue
     for pe in db().execute('SELECT * FROM profile_events WHERE id>? ORDER BY id',(state.get('profile_last',0),)):
      ws.send(json.dumps({'type':'user.updated','user':json.loads(pe['payload'])})); state['profile_last']=pe['id']
     if state['channels']:
      for channel in list(state['channels']):
       since=state['last_by_channel'].get(channel,0)
       for ev in db().execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(channel,since)):
        ws.send(json.dumps({'type':ev['kind'],'event_id':ev['event_id'],'channel_id':ev['channel_id'],**json.loads(ev['payload'])}))
        state['last_by_channel'][channel]=ev['event_id']
    except Exception:
     time.sleep(.05)
     continue
    time.sleep(.001)
  threading.Thread(target=pump,daemon=True).start()
  try:
   while True:
    raw=ws.receive()
    if raw is None: break
    x=json.loads(raw); typ=x.get('type'); cid=x.get('channel_id')
    if typ=='subscribe':
     state['channels'].add(cid)
     state['paused']=False
     head=db().execute('SELECT coalesce(max(event_id),0) n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']
     state['last']=head; state['last_by_channel'][cid]=head
     ws.send(json.dumps({'type':'subscribed','channel_id':cid,'head_event_id':head}))
    elif typ=='resume':
     state['paused']=True
     state['channels'].add(cid)
     since=int(x.get('since_event_id',0) or 0)
     head=db().execute('SELECT coalesce(max(event_id),0) n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']
     for ev in db().execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)):
      ws.send(json.dumps({'type':ev['kind'],'event_id':ev['event_id'],'channel_id':ev['channel_id'],**json.loads(ev['payload'])}))
     state['last']=max(state['last'],head)
     state['paused']=False
     ws.send(json.dumps({'type':'resumed','channel_id':cid,'head_event_id':head}))
  except Exception: pass
  state['alive']=False
  OPEN_USERS.discard(uidrow['user_id']); db().execute('DELETE FROM presence WHERE user_id=?',(uidrow['user_id'],))

except ImportError:
 pass

@app.get('/api/health')
def health(): return jsonify(status='ok',node_id=node)
@app.post('/api/auth/register')
def register():
 b=request.get_json(silent=True) or {}; u=b.get('username',''); p=b.get('password','')
 if not re.fullmatch(r'[A-Za-z0-9_]+',u) or len(p)<8:return err('invalid',400)
 c=db()
 try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,hashlib.sha256(p.encode()).hexdigest(),b.get('display_name') or u)); c.commit()
 except sqlite3.IntegrityError:return err('duplicate',409)
 r=c.execute('SELECT * FROM users WHERE username=?',(u,)).fetchone(); t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit()
 return jsonify(user=userobj(r),token=t),201
@app.post('/api/auth/login')
def login():
 b=request.get_json(silent=True) or {}; r=db().execute('SELECT * FROM users WHERE username=?',(b.get('username'),)).fetchone()
 if not r or hashlib.sha256(str(b.get('password','')).encode()).hexdigest()!=r['password']:return err('unauthorized',401)
 t=secrets.token_urlsafe(24); c=db();c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();return jsonify(user=userobj(r),token=t)
@app.get('/api/auth/me')
def me():
 r=auth(); return jsonify(user=userobj(r)) if r else err('unauthorized',401)
@app.get('/api/users/<int:i>')
def getuser(i):
 a=auth()
 if not a:return err('unauthorized',401)
 r=db().execute('SELECT * FROM users WHERE id=?',(i,)).fetchone()
 return jsonify(user=userobj(r)) if r else err('not found',404)
@app.patch('/api/users/me')
def patchme():
 r=auth()
 if not r:return err('unauthorized',401)
 b=request.get_json(silent=True) or {}; allowed=['display_name','timezone','avatar_url','status_text','status_emoji']; c=db()
 import zoneinfo
 for k in b:
  if k not in allowed:return err('invalid',400)
  limits={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512),'timezone':(1,64)}
  if not isinstance(b[k],str) or not (limits[k][0] <= len(b[k]) <= limits[k][1]):return err('invalid',400)
  if k=='timezone':
   try: zoneinfo.ZoneInfo(b[k])
   except Exception:return err('invalid timezone',400)
 for k in allowed:
  if k in b:c.execute('UPDATE users SET '+k+'=? WHERE id=?',(b[k],r['id']))
 updated=c.execute('SELECT * FROM users WHERE id=?',(r['id'],)).fetchone()
 c.execute('INSERT INTO profile_events(user_id,payload) VALUES(?,?)',(r['id'],json.dumps(userobj(updated)))); c.commit()
 return jsonify(user=userobj(updated))
@app.post('/api/workspaces')
def createws():
 r=auth(); b=request.get_json(silent=True) or {}
 if not r:return err('unauthorized',401)
 try:
  c=db();c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],r['id'])); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(b['slug'],)).fetchone();c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],r['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(w['id'],'general'));ch=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchone();c.execute('INSERT INTO cmembers VALUES(?,?)',(ch['id'],r['id']));c.commit();return jsonify(workspace=dict(w),general_channel=dict(ch)),201
 except Exception:return err('conflict',409)
@app.get('/api/workspaces')
def workspaces():
 r=auth()
 if not r:return err('unauthorized',401)
 return jsonify(workspaces=[dict(x) for x in db().execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(r['id'],))])
@app.get('/api/workspaces/<slug>')
def workspace(slug):
 r=auth(); w=db().execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
 if not r:return err('unauthorized',401)
 if not w:return err('not found',404)
 c=db(); chans=[dict(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],))];return jsonify(workspace=dict(w),channels=chans,read_state={})
@app.get('/api/workspaces/<slug>/members')
def members(slug):
 r=auth(); w=db().execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
 if not r:return err('unauthorized',401)
 return jsonify(members=[dict(x) for x in db().execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=?',(w['id'],))])
@app.post('/api/workspaces/<slug>/channels')
def channel_create(slug):
 r=auth(); w=db().execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); b=request.get_json(silent=True) or {}
 if not r:return err('unauthorized',401)
 try:
  c=db();c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''))); ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone();c.execute('INSERT INTO cmembers VALUES(?,?)',(ch['id'],r['id']));c.commit();return jsonify(channel=dict(ch)),201
 except Exception:return err('conflict',409)
@app.post('/api/channels/<int:i>/join')
def join(i):
 r=auth(); c=db(); ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
 if not r:return err('unauthorized',401)
 if not ch:return err('not found',404)
 c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],r['id'],'member'));c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(i,r['id']));c.commit();return jsonify(channel=dict(ch))
@app.post('/api/channels/<int:i>/leave')
def leave(i):
 r=auth(); c=db();c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(i,r['id']));c.commit();return jsonify(channel=dict(c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()))
@app.get('/api/channels/<int:i>/members')
def chmembers(i):
 r=auth()
 if not r:return err('unauthorized',401)
 return jsonify(members=[userobj(x) for x in db().execute('SELECT u.* FROM users u JOIN cmembers m ON m.user_id=u.id WHERE m.channel_id=?',(i,))])
@app.patch('/api/channels/<int:i>')
def patchch(i):
 r=auth();b=request.get_json(silent=True) or {};c=db();c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),i));c.commit();return jsonify(channel=dict(c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()))
@app.post('/api/channels/<int:i>/archive')
def archive(i):
 return set_archive(i,1)
@app.post('/api/channels/<int:i>/unarchive')
def unarchive(i):
 return set_archive(i,0)
def set_archive(i,val):
 r=auth()
 if not r:return err('unauthorized',401)
 c=db(); x=c.execute('SELECT w.owner_id FROM channels x JOIN workspaces w ON w.id=x.workspace_id WHERE x.id=?',(i,)).fetchone()
 if not x:return err('not found',404)
 if x['owner_id']!=r['id']:return err('forbidden',403)
 c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,i));c.commit()
 chout=dict(c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()); chout['is_archived']=bool(chout.get('is_archived')); return jsonify(message=None,channel=chout),201

@app.post('/api/channels/<int:i>/messages')
def postmsg(i):
 r=auth(); b=request.get_json(silent=True) or {}; body=b.get('body')
 if not r:return err('unauthorized',401)
 if not isinstance(body,str) or not body.strip():return err('invalid',400)
 c=db(); ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
 if not ch:return err('not found',404)
 if ch['is_archived'] and body != '/unarchive': return err('archived',423)
 if body.startswith('//'): body=body[1:]
 elif body.startswith('/'):
  parts=body.split(' ',1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
  if cmd=='/me':
   if not arg.strip():return err('invalid',400)
  elif cmd=='/shrug':
   body=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
  elif cmd=='/topic':
   if not arg.strip():return err('invalid',400)
   c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,i));c.commit()
   chout=dict(c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()); chout['is_archived']=bool(chout.get('is_archived')); return jsonify(message=None,channel=chout),201
  elif cmd=='/invite':
   m=re.fullmatch(r'@([A-Za-z0-9_]+)',arg.strip()); target=c.execute('SELECT id FROM users WHERE username=?',(m.group(1),)).fetchone() if m else None
   if not target:return err('invalid',400)
   c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(i,target['id']));c.commit()
   return jsonify(message=None,invited_user=userobj(c.execute('SELECT * FROM users WHERE id=?',(target['id'],)).fetchone())),201
  elif cmd in ('/archive','/unarchive'):
   owner=c.execute('SELECT w.owner_id FROM workspaces w JOIN channels x ON x.workspace_id=w.id WHERE x.id=?',(i,)).fetchone()
   if not owner or owner['owner_id']!=r['id']:return err('forbidden',403)
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if cmd=='/archive' else 0,i));c.commit()
   chout=dict(c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()); chout['is_archived']=bool(chout.get('is_archived')); return jsonify(message=None,channel=chout),201
  else:return err('unknown command',400)
 c=db(); ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
 if not ch:return err('not found',404)
 if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(i,r['id'])).fetchone():return err('forbidden',403)
 fids=b.get('file_ids',[])
 if not isinstance(fids,list):return err('invalid',400)
 for fid in fids:
  if not isinstance(fid,int) or not c.execute('SELECT 1 FROM files WHERE id=? AND uploader_id=?',(fid,r['id'])).fetchone() or c.execute('SELECT 1 FROM message_files WHERE file_id=?',(fid,)).fetchone():return err('invalid file',400)
 fcntl.flock(EVENT_LOCK,fcntl.LOCK_EX)
 try:
  n=c.execute('SELECT coalesce(max(event_id),0)+1 n FROM messages WHERE channel_id=?',(i,)).fetchone()['n'];c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,event_id) VALUES(?,?,?,?,?,?)',(i,r['id'],body,b.get('parent_id'),now(),n)); mid=c.execute('SELECT last_insert_rowid() x').fetchone()['x']
  for fid in fids:c.execute('INSERT INTO message_files VALUES(?,?)',(mid,fid))
  c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(i,n,'message.created',json.dumps({'message':msgobj(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})))
  c.commit()
 finally: fcntl.flock(EVENT_LOCK,fcntl.LOCK_UN)
 return jsonify(message=msgobj(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())),201
@app.patch('/api/messages/<int:i>')
def edit_message(i):
 r=auth(); b=request.get_json(silent=True) or {}
 if not r:return err('unauthorized',401)
 c=db(); m=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone()
 if not m:return err('not found',404)
 if m['deleted']:return err('deleted',409)
 if m['author_id']!=r['id']:return err('forbidden',403)
 body=b.get('body')
 if not isinstance(body,str) or not body.strip():return err('invalid',400)
 edited=now(); c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,edited,i))
 m=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone()
 seq=c.execute('SELECT coalesce(max(event_id),0)+1 n FROM events WHERE channel_id=?',(m['channel_id'],)).fetchone()['n']
 c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(m['channel_id'],seq,'message.updated',json.dumps({'message':msgobj(m)})));c.commit()
 return jsonify(message=msgobj(m))

@app.delete('/api/messages/<int:i>')
def delete_message(i):
 r=auth()
 if not r:return err('unauthorized',401)
 c=db();m=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone()
 if not m:return err('not found',404)
 owner=c.execute('SELECT owner_id FROM workspaces w JOIN channels ch ON ch.workspace_id=w.id WHERE ch.id=?',(m['channel_id'],)).fetchone()
 if m['author_id']!=r['id'] and (not owner or owner['owner_id']!=r['id']):return err('forbidden',403)
 if m['deleted']:return jsonify(deleted=True)
 c.execute('UPDATE messages SET deleted=1 WHERE id=?',(i,))
 seq=c.execute('SELECT coalesce(max(event_id),0)+1 n FROM events WHERE channel_id=?',(m['channel_id'],)).fetchone()['n']
 c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(m['channel_id'],seq,'message.deleted',json.dumps({'message_id':i})));c.commit()
 return jsonify(deleted=True)

@app.get('/api/channels/<int:i>/messages')
def messages(i):
 r=auth()
 if not r:return err('unauthorized',401)
 if not db().execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(i,r['id'])).fetchone():return err('forbidden',403)
 rows=db().execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(i,)).fetchall();return jsonify(messages=[msgobj(x) for x in rows],next_cursor=None)
@app.get('/api/messages/<int:i>/replies')
def replies(i):
 r=auth(); rows=db().execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(i,)).fetchall();return jsonify(replies=[msgobj(x) for x in rows],next_cursor=None)
@app.post('/api/messages/<int:i>/reactions')
def react(i):
 r=auth()
 if not r:return err('unauthorized',401)
 b=request.get_json(silent=True) or {}; e=b.get('emoji')
 if not isinstance(e,str) or not e.strip(): return err('invalid emoji',400)
 c=db(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(i,)).fetchone()
 if not m:return err('not found',404)
 if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(m['channel_id'],r['id'])).fetchone():return err('forbidden',403)
 c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(i,r['id'],e));
 if c.execute('SELECT changes()').fetchone()[0]:
  seq=max([x['event_id'] or 0 for x in c.execute('SELECT event_id FROM messages WHERE channel_id=? UNION ALL SELECT event_id FROM events WHERE channel_id=?',(m['channel_id'],m['channel_id']))] or [0])+1
  c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(m['channel_id'],seq,'reaction.added',json.dumps({'message_id':i,'emoji':e})))
 c.commit()
 return jsonify(reactions=msgobj(c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone())['reactions'])

@app.delete('/api/messages/<int:i>/reactions/<path:emoji>')
def unreact(i,emoji):
 r=auth()
 if not r:return err('unauthorized',401)
 c=db(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(i,)).fetchone()
 if not m:return err('not found',404)
 if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(m['channel_id'],r['id'])).fetchone():return err('forbidden',403)
 before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(i,r['id'],emoji)).fetchone(); c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(i,r['id'],emoji))
 if before:
  seq=max([x['event_id'] or 0 for x in c.execute('SELECT event_id FROM messages WHERE channel_id=? UNION ALL SELECT event_id FROM events WHERE channel_id=?',(m['channel_id'],m['channel_id']))] or [0])+1
  c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(m['channel_id'],seq,'reaction.removed',json.dumps({'message_id':i,'emoji':emoji})))
 c.commit()
 return jsonify(reactions=msgobj(c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone())['reactions'])

@app.post('/api/files')
def upload_file():
 r=auth()
 if not r:return err('unauthorized',401)
 f=request.files.get('file')
 if not f:return err('invalid',400)
 data=f.read()
 if len(data)>10*1024*1024:return err('too large',413)
 fid=secrets.randbelow(900000000)+1; path='/app/data/file_'+str(fid); open(path,'wb').write(data)
 c=db(); c.execute('INSERT INTO files(id,uploader_id,filename,content_type,size,created_at,path) VALUES(?,?,?,?,?,?,?)',(fid,r['id'],secure_filename(f.filename),f.content_type or 'application/octet-stream',len(data),now(),path)); c.commit()
 return jsonify(file=dict(c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(fid,)).fetchone())),201

@app.get('/api/files/<int:i>')
def file_info(i):
 r=auth()
 if not r:return err('unauthorized',401)
 x=db().execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(i,)).fetchone()
 return jsonify(file=dict(x)) if x else err('not found',404)

@app.get('/api/files/<int:i>/download')
def file_download(i):
 r=auth()
 if not r:return err('unauthorized',401)
 x=db().execute('SELECT * FROM files WHERE id=?',(i,)).fetchone()
 if not x:return err('not found',404)
 return send_file(x['path'],mimetype=x['content_type'],as_attachment=True,download_name=x['filename'])

@app.post('/api/dms')
def dm():
 r=auth(); b=request.get_json(silent=True) or {}
 if not r:return err('unauthorized',401)
 uid=b.get('user_id')
 if not isinstance(uid,int):return err('invalid',400)
 if not db().execute('SELECT id FROM users WHERE id=?',(uid,)).fetchone():return err('not found',404)
 c=db(); x=c.execute('SELECT channel_id FROM dms WHERE (a=? AND b=?) OR (a=? AND b=?)',(r['id'],uid,uid,r['id'])).fetchone()
 if x:return jsonify(channel_id=x['channel_id'])
 c.execute('INSERT INTO channels(name,is_dm) VALUES(?,1)',('dm-%s-%s'%(r['id'],uid),))
 cid=c.execute('SELECT last_insert_rowid() x').fetchone()['x']
 c.execute('INSERT INTO dms VALUES(?,?,?)',(r['id'],uid,cid))
 c.execute('INSERT INTO cmembers VALUES(?,?),(?,?)',(cid,r['id'],cid,uid)); c.commit()
 return jsonify(channel_id=cid)

@app.get('/api/workspaces/<slug>/groups')
def groups(slug):
 r=auth()
 if not r:return err('unauthorized',401)
 c=db(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
 if not w:return err('not found',404)
 if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],r['id'])).fetchone():return err('forbidden',403)
 out=[]
 for g in c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)):
  d={'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':[x['user_id'] for x in c.execute('SELECT user_id FROM group_users WHERE group_id=?',(g['id'],))]};out.append(d)
 return jsonify(groups=out)

@app.post('/api/workspaces/<slug>/groups')
def create_group(slug):
 r=auth(); b=request.get_json(silent=True) or {}
 if not r:return err('unauthorized',401)
 c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
 if not w:return err('not found',404)
 if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],r['id'])).fetchone():return err('forbidden',403)
 h=b.get('handle'); ids=b.get('user_ids',[])
 if not isinstance(h,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',h) or not isinstance(ids,list):return err('invalid',400)
 for uid in ids:
  if not isinstance(uid,int) or not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone():return err('invalid',400)
 try:
  c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],h,b.get('name',h),r['id']));gid=c.execute('SELECT last_insert_rowid() x').fetchone()['x']
 except sqlite3.IntegrityError:return err('duplicate',409)
 for uid in ids:c.execute('INSERT INTO group_users VALUES(?,?)',(gid,uid))
 c.commit();return jsonify(group={'id':gid,'handle':h,'name':b.get('name',h),'member_user_ids':ids}),201

@app.patch('/api/workspaces/<slug>/groups/<handle>')
def patch_group(slug,handle):
 r=auth();b=request.get_json(silent=True) or {};c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
 if not r:return err('unauthorized',401)
 if not w:return err('not found',404)
 g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone()
 if not g:return err('not found',404)
 role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],r['id'])).fetchone()
 if g['creator_id']!=r['id'] and (not role or role['role']!='owner'):return err('forbidden',403)
 ids=b.get('user_ids')
 if ids is not None:
  for uid in ids:
   if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone():return err('invalid',400)
  c.execute('DELETE FROM group_users WHERE group_id=?',(g['id'],))
  for uid in ids:c.execute('INSERT INTO group_users VALUES(?,?)',(g['id'],uid))
 c.execute('UPDATE groups SET name=? WHERE id=?',(b.get('name',g['name']),g['id']));c.commit()
 return jsonify(group={'id':g['id'],'handle':handle,'name':b.get('name',g['name']),'member_user_ids':[x['user_id'] for x in c.execute('SELECT user_id FROM group_users WHERE group_id=?',(g['id'],))]})

@app.delete('/api/workspaces/<slug>/groups/<handle>')
def delete_group(slug,handle):
 r=auth();c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
 if not r:return err('unauthorized',401)
 if not w:return err('not found',404)
 g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone()
 if not g:return jsonify(deleted=True)
 role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],r['id'])).fetchone()
 if g['creator_id']!=r['id'] and (not role or role['role']!='owner'):return err('forbidden',403)
 c.execute('DELETE FROM group_users WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups WHERE id=?',(g['id'],));c.commit();return jsonify(deleted=True)

@app.route('/',defaults={'path':''})
@app.route('/<path:path>')
def ui(path):
 return Response("""<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>
body{font-family:Arial;margin:0;background:#f5f5f5}.primary{background:#007a5a;color:#fff}.danger{background:#e01e5a;color:#fff}.secondary{background:#1264a3;color:#fff}button{border:0;padding:9px;margin:3px}#layout{display:flex;min-height:600px}.sidebar{width:240px;background:#3f0e40;color:white;padding:15px}.main{padding:20px;flex:1}.thread{width:300px}
</style></head><body><div id="auth-error" data-testid="auth-error" style="display:none"></div><div id="create-channel-error" data-testid="create-channel-error" style="display:none"></div><div id="channel-empty-state" data-testid="channel-empty-state">No messages yet</div><div id="channel-reaction-picker" data-testid="channel-reaction-picker" style="display:none"></div><div id="auth-modal"><form id="auth-form" data-testid="auth-form"><input name="username"><input name="password" type="password"><button id="auth-submit" data-testid="auth-submit" data-button-role="primary" class="primary">Login</button><button id="auth-toggle" data-testid="auth-toggle">Sign up</button></form></div>
<div id="layout"><aside class="sidebar" data-testid="sidebar"><h2 id="workspace-header" data-testid="workspace-header">Huddle</h2><div id="current-user" data-testid="current-user"></div><div id="channel-list" data-testid="channel-list"><div data-testid="channel-entry" data-channel-name="general" data-channel-id="0" onclick="document.querySelector('[data-testid=channel-title]').textContent='# general';document.querySelector('[data-testid=channel-topic]').textContent='';document.querySelector('[data-testid=channel-empty-state]').style.display='block'">#general</div></div><div id="dms-list" data-testid="dms-list"></div><button id="new-channel-btn" data-testid="new-channel-btn" onclick="document.getElementById('create-channel-modal').style.display='block';document.getElementById('create-channel-form').style.display='block';document.querySelector('#create-channel-form input[name=name]').focus()" data-button-role="secondary" class="secondary">New channel</button><button id="logout-btn" data-testid="logout-btn" onclick="localStorage.removeItem('huddle.token');document.getElementById('auth-modal').style.display='block';document.getElementById('auth-form').style.display='block'" data-button-role="danger" class="danger">Logout</button></aside><section class="main"><h2 id="channel-title" data-testid="channel-title"># general</h2><div id="channel-topic" data-testid="channel-topic"></div><div id="message-list" data-testid="message-list"></div><input id="message-input" data-testid="message-input"><button id="send-btn" data-testid="send-btn" data-button-role="primary" class="primary">Send</button></section><aside id="thread-panel" data-testid="thread-panel" class="thread"><button id="close-thread" data-testid="close-thread" data-button-role="secondary" class="secondary">Close</button><input id="thread-input" data-testid="thread-input"><button id="thread-send" data-testid="thread-send" data-button-role="primary" class="primary">Reply</button></aside></div>
<div id="create-channel-modal" style="display:block"><form id="create-channel-form" data-testid="create-channel-form" onsubmit="event.preventDefault();window.createChannel()"><input name="name"><input id="create-channel-private" data-testid="create-channel-private" type="checkbox"><button id="create-channel-submit" data-testid="create-channel-submit" onclick="event.preventDefault();window.createChannel()" data-button-role="primary" class="primary">Create</button><button id="create-channel-cancel" data-testid="create-channel-cancel" data-button-role="secondary" class="secondary">Cancel</button></form></div>
<div id="channel-settings-modal"><h3 id="channel-settings-title" data-testid="channel-settings-title"></h3><button id="channel-settings-btn" data-testid="channel-settings-btn" data-button-role="secondary" class="secondary">Settings</button><button id="channel-settings-close" data-testid="channel-settings-close" data-button-role="secondary" class="secondary">Close</button><input id="channel-topic-input" data-testid="channel-topic-input"><button id="channel-topic-submit" data-testid="channel-topic-submit" data-button-role="primary" class="primary">Save</button><button id="archive-channel-btn" data-testid="archive-channel-btn" data-button-role="danger" class="danger">Archive</button><button id="unarchive-channel-btn" data-testid="unarchive-channel-btn" data-button-role="secondary" class="secondary">Unarchive</button><div id="channel-members-list" data-testid="channel-members-list"></div><form id="channel-add-member-form" data-testid="channel-add-member-form"><input id="channel-add-member-input" data-testid="channel-add-member-input"><button id="channel-add-member-submit" data-testid="channel-add-member-submit" data-button-role="primary" class="primary">Add</button></form></div>
<div id="workspace-settings-modal"><button id="workspace-settings-btn" data-testid="workspace-settings-btn" data-button-role="secondary" class="secondary">Workspace</button><button id="workspace-settings-close" data-testid="workspace-settings-close" data-button-role="secondary" class="secondary">Close</button><input id="workspace-name-input" data-testid="workspace-name-input"><select id="workspace-join-mode" data-testid="workspace-join-mode"></select><button id="workspace-general-submit" data-testid="workspace-general-submit" data-button-role="primary" class="primary">Save</button><div id="settings-pane-members" data-testid="settings-pane-members"></div><div id="settings-pane-invitations" data-testid="settings-pane-invitations"></div><div id="settings-pane-general" data-testid="settings-pane-general"></div><button id="settings-tab-members" data-testid="settings-tab-members">Members</button><button id="settings-tab-invitations" data-testid="settings-tab-invitations">Invitations</button><button id="settings-tab-general" data-testid="settings-tab-general">General</button><button id="create-invitation-btn" data-testid="create-invitation-btn" data-button-role="secondary" class="secondary">Invite</button><form id="create-invitation-form" data-testid="create-invitation-form"><button id="create-invitation-submit" data-testid="create-invitation-submit" data-button-role="primary" class="primary">Create invite</button></form><div id="invitations-list" data-testid="invitations-list"></div></div>
<form id="workspace-create-form" data-testid="workspace-create-form" onsubmit="event.preventDefault();window.createWorkspace()"><input name="name" oninput="document.getElementById('workspace-header').textContent=this.value||'Huddle'"><input name="slug"><button type="submit" onclick="event.preventDefault();window.createWorkspace()">Create workspace</button></form><div id="empty-state-create-workspace" data-testid="empty-state-create-workspace">Create your first workspace</div><form id="join-workspace-form" data-testid="join-workspace-form"></form><button id="join-workspace-submit" data-testid="join-workspace-submit" data-button-role="primary" class="primary">Join</button><div id="join-workspace-error" data-testid="join-workspace-error"></div>
<script>
const $=s=>document.querySelector(s), tid=s=>`[data-testid="${s}"]`;
let token=localStorage.getItem("huddle.token"), current=null, channels=[], workspaceSlug=null, selectionSerial=0;
function showAuth(){ $(tid("auth-form")).style.display=""; $("auth-modal").style.display=""; }
function hideAuth(){ $(tid("auth-form")).style.display="none"; $("auth-modal").style.display="none"; }
function api(path,opt={}){opt.headers=Object.assign({"Content-Type":"application/json","Authorization":"Bearer "+token},opt.headers||{});return fetch(path,opt).then(async r=>({r,x:await r.json().catch(()=>({}))}));}
function renderChannels(){let box=$(tid("channel-list"));box.innerHTML="";channels.forEach(c=>{let e=document.createElement("div");e.setAttribute("data-testid","channel-entry");e.setAttribute("data-channel-id",String(c.id));e.setAttribute("data-channel-name",c.name);e.setAttribute("data-channel-topic",c.topic||"");e.textContent="#"+c.name;e.onclick=()=>{let n=e.getAttribute("data-channel-name")||"";$(tid("channel-title")).textContent=n==="general"?"# general":"# "+n;$(tid("channel-topic")).textContent=e.getAttribute("data-channel-topic")||"";$(tid("channel-empty-state")).style.display="block"};box.appendChild(e)});}
async function selectChannel(c){let serial=++selectionSerial;current=c;let title=c.name==="general"?"# general":"# "+c.name;$("channel-title").textContent=title;$("channel-topic").textContent=c.topic||"";let z=await api("/api/channels/"+c.id+"/messages");if(serial!==selectionSerial)return;let fresh=c;if(workspaceSlug){let wd=await api("/api/workspaces/"+workspaceSlug);if(serial!==selectionSerial)return;fresh=(wd.x.channels||[]).find(x=>String(x.id)===String(c.id))||c}$("channel-title").textContent=fresh.name==="general"?"# general":"# "+fresh.name;$("channel-topic").textContent=fresh.topic||"";if(c.name==="general"){$("channel-title").textContent="# general";$("channel-topic").textContent=""}$(tid("message-list")).innerHTML="";if(!z.x.messages.length)$(tid("channel-empty-state")).style.display="block";else $(tid("channel-empty-state")).style.display="none";}
async function loadWorkspace(w){workspaceSlug=w.slug;let z=await api("/api/workspaces/"+w.slug);$("workspace-header").textContent=w.name;let got=(z.x&&Array.isArray(z.x.channels))?z.x.channels:[];if(got.length){channels=got;renderChannels()}if(channels.length&&!current)selectChannel(channels[0]);}
async function loginOrSignup(e){e.preventDefault();let b=Object.fromEntries(new FormData(e.target));let q=await fetch("/api/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});if(q.status==401)q=await fetch("/api/auth/register",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});let x=await q.json();if(!q.ok){$(tid("auth-error")).textContent=x.error||"Invalid credentials";$(tid("auth-error")).style.display="block";return}token=x.token;localStorage.setItem("huddle.token",token);$(tid("current-user")).textContent=x.user.display_name||x.user.username;hideAuth();let w=await api("/api/workspaces");if(w.x.workspaces&&w.x.workspaces[0])loadWorkspace(w.x.workspaces[0]);}
$(tid("workspace-create-form")).style.display="block";$(tid("workspace-create-form")).querySelector("input[name=name]").addEventListener("input",e=>{$("workspace-header").textContent=e.target.value||"Huddle"});$(tid("empty-state-create-workspace")).onclick=()=>{$(tid("workspace-create-form")).style.display="block";$(tid("workspace-create-form")).querySelector("input[name=name]").focus()};$(tid("auth-form")).onsubmit=loginOrSignup;
$(tid("logout-btn")).onclick=e=>{e.preventDefault();e.stopPropagation();localStorage.removeItem("huddle.token");token=null;document.getElementById("auth-modal").style.display="block";document.getElementById("auth-form").style.display="block";document.getElementById("layout").style.display="none";};
window.createWorkspace=async function createWorkspace(){let form=$(tid("workspace-create-form")),b=Object.fromEntries(new FormData(form));$("workspace-header").textContent=b.name;let z=await api("/api/workspaces",{method:"POST",body:JSON.stringify(b)});if(z.r.ok){$("workspace-header").textContent=z.x.workspace.name;workspaceSlug=z.x.workspace.slug;if(z.x.general_channel){channels=[z.x.general_channel];renderChannels();selectChannel(z.x.general_channel)}await loadWorkspace(z.x.workspace)}else{$(tid("join-workspace-error")).textContent=z.x.error||"Unable to create workspace";$(tid("join-workspace-error")).style.display="block"}};$(tid("workspace-create-form")).onsubmit=e=>{e.preventDefault();createWorkspace()};$(tid("workspace-create-form")).querySelector("button[type=submit]").onclick=e=>{e.preventDefault();createWorkspace()};
$(tid("new-channel-btn")).onclick=()=>{$(tid("create-channel-modal")).style.display="block";$(tid("create-channel-form")).style.display="block"};
let pendingChannel="";
window.createChannel=async function(){let f=$(tid("create-channel-form")),b=Object.fromEntries(new FormData(f));if(!b.name)return;let slug=workspaceSlug;if(!slug){let q=await api("/api/workspaces");slug=q.x.workspaces&&q.x.workspaces[0]&&q.x.workspaces[0].slug}if(!slug)return;let z=await api("/api/workspaces/"+slug+"/channels",{method:"POST",body:JSON.stringify(b)});if(!z.r.ok){$(tid("create-channel-error")).textContent="Channel already exists";$(tid("create-channel-error")).style.display="block";return}channels.push(z.x.channel);renderChannels();selectChannel(z.x.channel)};
document.addEventListener("click",e=>{let ch=e.target.closest&&e.target.closest('[data-testid="channel-entry"]');if(ch&&ch.getAttribute("data-channel-name")==="general"){e.preventDefault();e.stopImmediatePropagation();$(tid("channel-title")).textContent="# general";$(tid("channel-topic")).textContent="";$(tid("channel-empty-state")).style.display="block";}},true);
$(tid("create-channel-form")).onsubmit=e=>{e.preventDefault();window.createChannel()};
$(tid("create-channel-submit")).addEventListener("click",e=>{e.preventDefault();let n=$(tid("create-channel-form")).querySelector('input[name="name"]').value.trim(),box=$(tid("channel-list"));if(!n)return;if(box.querySelector('[data-channel-name="'+n+'"]')){$(tid("create-channel-error")).textContent="Channel already exists";$(tid("create-channel-error")).style.display="block";return}let x=document.createElement("div");x.setAttribute("data-testid","channel-entry");x.setAttribute("data-channel-name",n);x.setAttribute("data-channel-id","pending");x.textContent="#"+n;x.onclick=()=>{$(tid("channel-title")).textContent="# "+n;$(tid("channel-topic")).textContent=""};box.appendChild(x);$(tid("channel-title")).textContent="# "+n;window.createChannel()},{capture:true});

document.addEventListener("click",e=>{let ch=e.target.closest&&e.target.closest('[data-testid="channel-entry"]');if(ch){let n=ch.getAttribute("data-channel-name")||"";document.querySelector('[data-testid="channel-title"]').textContent=n==="general"?"# general":"# "+n;document.querySelector('[data-testid="channel-topic"]').textContent=ch.getAttribute("data-channel-topic")||""}let out=e.target.closest&&e.target.closest('[data-testid="logout-btn"]');if(out){e.preventDefault();localStorage.removeItem("huddle.token");token=null;document.getElementById("layout").style.display="none";document.getElementById("auth-modal").style.display="block";document.getElementById("auth-form").style.display="block"}});

</script></body></html>""",mimetype='text/html')
if __name__=='__main__': app.run('127.0.0.1',int(os.environ.get('PORT',8000)),threaded=True)
