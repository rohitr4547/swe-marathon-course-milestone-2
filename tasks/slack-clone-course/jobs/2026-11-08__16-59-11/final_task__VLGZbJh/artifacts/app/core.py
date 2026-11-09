import sqlite3, os, secrets, hashlib, hmac, re, base64, json
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
from contextlib import contextmanager
@contextmanager
def tx():
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
    except: c.execute('ROLLBACK'); raise
    finally: c.close()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    os.makedirs('/app/data',exist_ok=True)
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS members(user_id INTEGER,workspace_id INTEGER,channel_id INTEGER,role TEXT DEFAULT 'member',PRIMARY KEY(user_id,workspace_id,channel_id));
    CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY,workspace_id INTEGER,code TEXT UNIQUE,created_by INTEGER,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,expires_at REAL,invited_user_id INTEGER,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,created_at TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,created_at TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    '''); c.close()
def hash_password(pw):
    salt=secrets.token_hex(16); return salt+'$'+hashlib.sha256((salt+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split('$',1); return hmac.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,user_id):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?,?)',(t,user_id,now_iso())); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
if __name__=='__main__':
    init_db()

def workspace_to_json(r):
    return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member_role(workspace_id,user_id):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=? AND channel_id IS NULL',(workspace_id,user_id)).fetchone(); c.close(); return r['role'] if r else None
def channel_to_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def add_channel_member(c,channel_id,user_id):
    ch=c.execute('SELECT workspace_id FROM channels WHERE id=?',(channel_id,)).fetchone()
    try:c.execute('INSERT INTO members(user_id,workspace_id,channel_id,role) VALUES(?,?,?,?)',(user_id,ch['workspace_id'],channel_id,'member')); return True
    except sqlite3.IntegrityError:return False
def add_workspace_member(c,workspace_id,user_id,role='member'):
    try:c.execute('INSERT INTO members(user_id,workspace_id,channel_id,role) VALUES(?,?,NULL,?)',(user_id,workspace_id,role))
    except sqlite3.IntegrityError: pass
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(workspace_id,)).fetchone()
    if g: add_channel_member(c,g['id'],user_id)
def remove_channel_member(c,channel_id,user_id):
    x=c.execute('DELETE FROM members WHERE channel_id=? AND user_id=?',(channel_id,user_id)); return x.rowcount>0
def emit_event(c,channel_id,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(channel_id,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':channel_id}; frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(channel_id,n,json.dumps(frame))); return frame
def can_read_channel(user_id,ch):
    if not ch:return False
    r=connect(); m=r.execute('SELECT 1 FROM members WHERE channel_id=? AND user_id=?',(ch['id'],user_id)).fetchone()
    if m:return True
    if ch['is_private'] or not ch['workspace_id']:return False
    m=r.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=? AND channel_id IS NULL",(ch['workspace_id'],user_id)).fetchone(); r.close()
    return bool(m and m['role']!='guest')
def require_writable(ch):
    if ch and ch['is_archived']: raise ValueError('archived')
def validate_topic(v):
    if not isinstance(v,str) or len(v)>250: raise ValueError('invalid topic')
    return v
def join_channel(user,channel_id):
    ch=get_channel(channel_id)
    if not ch:return None
    with tx() as c:
        if ch['workspace_id'] and not member_role(ch['workspace_id'],user['id']):
            ws=c.execute('SELECT join_mode FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            if ws and ws['join_mode']=='open' and not ch['is_private']: add_workspace_member(c,ch['workspace_id'],user['id'])
        add_channel_member(c,channel_id,user['id'])
    return get_channel(channel_id)
def create_channel_row(c,workspace_id,name,is_private=False,topic=''):
    q=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(workspace_id,name,int(is_private),topic))
    i=q.lastrowid
    c.execute('INSERT INTO members(user_id,workspace_id,channel_id,role) SELECT owner_id,?, ?, "member" FROM workspaces WHERE id=?',(workspace_id,i,workspace_id))
    return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def get_workspace_by_id(i):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(i,)).fetchone(); c.close(); return r
