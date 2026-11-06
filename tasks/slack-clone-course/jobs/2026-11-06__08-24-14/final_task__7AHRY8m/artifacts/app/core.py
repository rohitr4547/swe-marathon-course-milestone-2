import sqlite3, os, json, secrets, hashlib, base64, re, argparse, time
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
class tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    os.makedirs('/app/data/files',exist_ok=True)
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER);
CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
'''); c.close()
def hash_password(pw):
    salt=secrets.token_hex(16); return salt+'$'+hashlib.sha256((salt+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?,?)',(t,uid,now_iso())); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    if not r: return None
    d=dict(r); d.pop('password',None); return d
def workspace_to_json(r): return dict(r) if r else None
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def create_channel_row(c,wid,name,private=False,topic=''):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic)); return c.execute('SELECT * FROM channels WHERE id=?',(cur.lastrowid,)).fetchone()
def add_channel_member(c,cid,uid): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid))
def remove_channel_member(c,cid,uid): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid))
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g:add_channel_member(c,g['id'],uid)
def channel_to_json(r):
    if not r:return None
    d=dict(r); d['is_private']=bool(d.get('is_private')); d['is_dm']=bool(d.get('is_dm')); d['is_archived']=bool(d.get('is_archived')); return d
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def can_read_channel(uid,ch):
    c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close(); return bool(r)
def require_writable(ch):
    if ch and ch['is_archived']: raise ValueError('archived')
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':cid,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return frame
def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(cid):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r[0] if r else 0
def events_after(cid,since):
    c=connect(); x=[json.loads(r[0]) for r in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since))]; c.close(); return x
def globals_after(since):
    c=connect(); x=[(r[0],json.loads(r[1])) for r in c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,))]; c.close(); return x
def encode_cursor(kind,v):
    return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':int(v)}).encode()).decode().rstrip('=')
def decode_cursor(kind,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); assert x['k']==kind and isinstance(x['v'],int); return x['v']
    except: raise ValueError('invalid cursor')
def create_message(user,cid,body,parent=None,file_ids=None):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not ch or not can_read_channel(user['id'],ch): raise PermissionError()
        require_writable(ch)
        file_ids=[] if file_ids is None else file_ids
        if not isinstance(file_ids,list) or any(not isinstance(x,int) for x in file_ids): raise ValueError('files')
        for fid in file_ids:
            f=c.execute('SELECT uploader_id,message_id FROM files WHERE id=?',(fid,)).fetchone()
            if not f or f['uploader_id']!=user['id'] or f['message_id'] is not None: raise ValueError('files')
        t=now_iso()
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,user['id'],body,parent,t))
        for fid in file_ids: c.execute('UPDATE files SET message_id=? WHERE id=?',(cur.lastrowid,fid))
        r=c.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone()
        emit_event(c,cid,'message.reply' if parent else 'message.created',{'message':message_to_json(r)})
        return r

def reactions_for(mid):
    c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)).fetchall(); c.close(); d={}
    for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
    return [{'emoji':k,'count':len(v),'user_ids':v} for k,v in d.items()]
def message_to_json(r):
    if not r:return None
    d=dict(r); c=connect()
    u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    n=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    fs=c.execute('SELECT * FROM files WHERE message_id=? ORDER BY id',(r['id'],)).fetchall()
    ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    mentions=resolve_mentions(c,r['body'],ch,r['author_id']) if ch else []
    c.close()
    d['author']=user_to_json(u); d['reply_count']=n; d['files']=[file_to_json(f) for f in fs]; d['reactions']=reactions_for(r['id']); d['mentions']=mentions; return d

if __name__=='__main__':
    if '--init-db' in __import__('sys').argv:init_db()
def get_workspace_by_id(wid):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); c.close(); return r
def list_messages(cid,limit=50,cursor=None):
    c=connect(); args=[cid]; q='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0'
    if cursor is not None:q+=' AND id<?'; args.append(cursor)
    q+=' ORDER BY id DESC LIMIT ?'; args.append(limit+1); rows=c.execute(q,args).fetchall(); c.close()
    return rows
def list_replies(pid,limit=50,cursor=None):
    c=connect(); args=[pid]; q='SELECT * FROM messages WHERE parent_id=? AND deleted=0'
    if cursor is not None:q+=' AND id>?'; args.append(cursor)
    q+=' ORDER BY id ASC LIMIT ?'; args.append(limit+1); rows=c.execute(q,args).fetchall(); c.close()
    return rows
def resolve_mentions(c,body,channel,author_id):
    ids=set()
    if re.search(r'(?<!\w)@channel(?!\w)',body):
        for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],)): ids.add(r[0])
    for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_-]+)',body):
        if channel['workspace_id'] and name == 'here':
            for r in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)):
                if c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel['id'],r[0])).fetchone(): ids.add(r[0])
            continue
        if channel['workspace_id']:
            group_ids=groups_for_mentions(c,name,channel['workspace_id'])
            if group_ids:
                for x in group_ids:
                    if c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel['id'],x)).fetchone(): ids.add(x)
                continue
        r=c.execute('SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.workspace_id=? AND u.username=?',(channel['workspace_id'],name)).fetchone()
        if r:ids.add(r[0])
    ids.discard(author_id); return list(ids)
def run_slash(user,channel,body):
    if not isinstance(body,str) or not body.startswith('/'): return ('message',body)
    if body.startswith('//'): return ('message',body[1:])
    parts=body.split(' ',1); cmd=parts[0][1:]; arg=parts[1] if len(parts)>1 else ''
    if cmd=='me':
        if not arg.strip(): raise ValueError('command')
        return ('message',body)
    if cmd=='shrug':
        return ('message',(arg+' ' if arg else '')+'¯\\_(ツ)_/¯')
    if cmd=='topic':
        if not arg.strip(): raise ValueError('command')
        if not can_read_channel(user['id'],channel): raise PermissionError()
        with tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,channel['id']))
        return ('topic',get_channel(channel['id']))
    if cmd=='invite':
        m=re.fullmatch(r'@([A-Za-z0-9_]+)',arg.strip())
        if not m: raise ValueError('command')
        c=connect(); target=c.execute('SELECT * FROM users WHERE username=?',(m.group(1),)).fetchone(); c.close()
        if not target: raise ValueError('command')
        with tx() as c:
            c.execute('INSERT OR IGNORE INTO memberships(workspace_id,user_id,role) VALUES(?,?,?)',(channel['workspace_id'],target['id'],'member'))
            c.execute('INSERT OR IGNORE INTO channel_members(channel_id,user_id) VALUES(?,?)',(channel['id'],target['id']))
        return ('invite',target)
    if cmd in ('archive','unarchive'):
        owner=get_workspace_by_id(channel['workspace_id']) if channel['workspace_id'] else None
        if not owner or owner['owner_id']!=user['id']: raise PermissionError()
        with tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if cmd=='archive' else 0,channel['id']))
        return ('channel',get_channel(channel['id']))
    raise ValueError('command')

def react(user,mid,emoji,add=True):
    with tx() as c:
        r=c.execute('SELECT m.*,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(mid,)).fetchone()
        if not r: raise LookupError()
        if r['deleted']: return reactions_for(mid)
        if r['is_archived']: raise ValueError('archived')
        member=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],user['id'])).fetchone()
        if not member: raise PermissionError()
        exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji)).fetchone()
        changed=False
        if add and not exists:
            c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,user['id'],emoji)); changed=True
        elif not add and exists:
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji)); changed=True
        if changed:
            emit_event(c,r['channel_id'],'reaction.added' if add else 'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':user['id']})
    return reactions_for(mid)

def edit_message(user,mid,body):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r:raise LookupError()
        if r['author_id']!=user['id']:raise PermissionError()
        if r['deleted']:raise ValueError()
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,now_iso(),mid)); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        emit_event(c,r['channel_id'],'message.updated',{'message':message_to_json(r)})
    return r
def delete_message(user,mid):
    with tx() as c:
        r=c.execute('SELECT m.*,w.owner_id FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN workspaces w ON w.id=ch.workspace_id WHERE m.id=?',(mid,)).fetchone()
        if not r: raise LookupError()
        if r['author_id']!=user['id'] and r['owner_id']!=user['id']:raise PermissionError()
        if r['deleted']: return True
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid})
    return True
def open_dm(user, other_id):
    with tx() as c:
        other=c.execute('SELECT * FROM users WHERE id=?',(other_id,)).fetchone()
        if not other: raise LookupError()
        a,b=sorted((user['id'],other_id)); r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if r: return r[0]
        cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',(f'dm-{a}-{b}',1))
        cid=cur.lastrowid; c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid))
        c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,b))
        return cid
def save_file(user,filename,content_type,data):
    if len(data)>10*1024*1024: raise OverflowError()
    with tx() as c:
        cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(user['id'],filename,content_type,len(data),now_iso()))
        fid=cur.lastrowid
        with open(f'/app/data/files/{fid}','wb') as f:f.write(data)
        return c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
def file_to_json(row):
    return dict(row) if row else None
def group_to_json(r):
    if not r:return None
    c=connect(); ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id',(r['id'],))]; c.close()
    d=dict(r); d.pop('creator_id',None); d['member_user_ids']=ids; return d
def create_group(uid,wid,handle,name,user_ids):
    with tx() as c:
        if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone(): raise FileExistsError()
        for x in user_ids:
            if not c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(wid,x)).fetchone(): raise ValueError()
        r=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(wid,handle,name,uid)).lastrowid
        for x in user_ids:c.execute('INSERT INTO group_members VALUES(?,?)',(r,x))
        return c.execute('SELECT * FROM groups_ WHERE id=?',(r,)).fetchone()
def update_group(wid,handle,fields):
    with tx() as c:
        r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone()
        if not r:raise LookupError()
        if 'name' in fields:c.execute('UPDATE groups_ SET name=? WHERE id=?',(fields['name'],r['id']))
        if 'user_ids' in fields:
            for x in fields['user_ids']:
                if not c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(wid,x)).fetchone():raise ValueError()
            c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],))
            for x in fields['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(r['id'],x))
        return c.execute('SELECT * FROM groups_ WHERE id=?',(r['id'],)).fetchone()
def delete_group(wid,handle):
    with tx() as c:
        r=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone()
        if not r:raise LookupError()
        c.execute('DELETE FROM group_members WHERE group_id=?',(r[0],)); c.execute('DELETE FROM groups_ WHERE id=?',(r[0],))
def groups_for_mentions(c,handle,wid):
    r=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone()
    return [x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(r[0],))] if r else []

def touch_presence(conn_id,user_id):
    with tx() as c:c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
    with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
def update_profile(user,fields):
    allowed={'display_name':80,'timezone':128,'status_text':140,'status_emoji':32,'avatar_url':512}
    if not isinstance(fields,dict): raise ValueError()
    from zoneinfo import ZoneInfo
    vals={}
    for k,v in fields.items():
        if k not in allowed or not isinstance(v,str) or not v or len(v)>allowed[k]: raise ValueError()
        if k=='timezone':
            try: ZoneInfo(v)
            except: raise ValueError()
        vals[k]=v
    with tx() as c:
        if vals:
            c.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',tuple(vals.values())+(user['id'],))
        r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        emit_global(c,{'type':'user.updated','user':user_to_json(r)})
    return r
