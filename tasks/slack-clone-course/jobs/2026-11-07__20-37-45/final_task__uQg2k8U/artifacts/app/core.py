import sqlite3, os, sys, json, base64, hashlib, secrets, re, datetime, time, threading
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); c.execute('PRAGMA foreign_keys=ON'); return c
class tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE INDEX IF NOT EXISTS ix_messages_channel ON messages(channel_id,id);
'''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+':'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split(':',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,user_id):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?,?)',(t,user_id,now_iso())); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
    return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u))
def create_channel_row(c,w,name,private=False,topic=''):
    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic)); return c.execute('SELECT * FROM channels WHERE id=?',(x.lastrowid,)).fetchone()
def add_channel_member(c,ch,u):
    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def remove_channel_member(c,ch,u): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u))
def join_channel(user,channel_id):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(channel_id,)).fetchone()
        if not ch:return None
        if ch['workspace_id']:
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            add_workspace_member(c,w['id'],user['id'],'member')
        add_channel_member(c,channel_id,user['id']); return c.execute('SELECT * FROM channels WHERE id=?',(channel_id,)).fetchone()
def can_read_channel(uid,ch):
    c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close(); return bool(r)
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':ch}; frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(ch):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r['last_event_id'] if r else 0
def events_after(ch,since):
    c=connect(); a=[json.loads(r['payload']) for r in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since))]; c.close(); return a
def globals_after(since):
    c=connect(); a=[(r['id'],json.loads(r['payload'])) for r in c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,))]; c.close(); return a
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':int(v)}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); assert x.get('k')==k and type(x.get('v')) is int; return x['v']
    except: raise ValueError()
def parse_paging(q,k):
    try: n=int(q.get('limit','50')); assert 1<=n<=200; cur=decode_cursor(k,q['cursor']) if q.get('cursor') else None; return n,cur
    except: raise ValueError()
def message_to_json(r):
    c=connect(); a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    reps=c.execute('SELECT author_id FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchall(); c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':len(reps),'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[],'mentions':[]}
def resolve_mentions(c,body,channel,author_id):
    ids=set(); names=re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body)
    members=[x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],))]
    for n in names:
        if n=='channel': ids.update(x for x in members if x!=author_id); continue
        r=c.execute('SELECT id FROM users WHERE username=?',(n,)).fetchone()
        if r and r['id']!=author_id and r['id'] in members: ids.add(r['id'])
    return list(ids)
def create_message(user,ch,body,parent_id=None):
    with tx() as c:
        channel=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
        if not channel or not can_read_channel(user['id'],channel): raise PermissionError()
        if channel['is_archived']: raise RuntimeError()
        if not isinstance(body,str) or not body.strip(): raise ValueError()
        ts=now_iso(); x=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,user['id'],body,parent_id,ts))
        r=c.execute('SELECT * FROM messages WHERE id=?',(x.lastrowid,)).fetchone(); kind='message.reply' if parent_id else 'message.created'
        m=message_to_json(r); m['mentions']=resolve_mentions(c,body,channel,user['id']); emit_event(c,ch,kind,{'message':m}); return m
def list_messages(ch,limit=50,cursor=None):
    c=connect(); q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'; p=[ch]
    if cursor is not None:q+=' AND id<?';p.append(cursor)
    q+=' ORDER BY id DESC LIMIT ?';p.append(limit+1); rows=c.execute(q,p).fetchall(); c.close(); a=[message_to_json(x) for x in rows[:limit]]; return a,encode_cursor('messages',rows[limit-1]['id']) if len(rows)>limit else None
def list_replies(pid,limit=50,cursor=None):
    c=connect(); q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';p=[pid]
    if cursor is not None:q+=' AND id>?';p.append(cursor)
    q+=' ORDER BY id LIMIT ?';p.append(limit+1);rows=c.execute(q,p).fetchall();c.close();return [message_to_json(x) for x in rows[:limit]],(encode_cursor('replies',rows[limit-1]['id']) if len(rows)>limit else None)
def edit_message(user,mid,body):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r or r['author_id']!=user['id']:raise PermissionError()
        if not body.strip():raise ValueError()
        ts=now_iso();c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,ts,mid));r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();m=message_to_json(r);emit_event(c,r['channel_id'],'message.updated',{'message':m});return m
def delete_message(user,mid):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r:return
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid})
def touch_presence(cid,uid):
    c=connect();c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(cid,uid,time.time()));c.close()
def drop_presence(cid):
    c=connect();c.execute('DELETE FROM presence WHERE conn_id=?',(cid,));c.close()
def present_user_ids():
    c=connect();a=[r['user_id'] for r in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))];c.close();return a
if __name__=='__main__' and '--init-db' in sys.argv:init_db()
