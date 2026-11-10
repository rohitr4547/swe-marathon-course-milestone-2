import sqlite3, os, json, hashlib, secrets, base64, re, datetime, argparse, time
from contextlib import contextmanager
from zoneinfo import ZoneInfo
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000'); return c
@contextmanager
def tx():
    c=connect()
    try: c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
    except: c.execute('ROLLBACK'); raise
    finally: c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    os.makedirs('/app/data/files',exist_ok=True); c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    '''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+':'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,st):
    try: s,h=st.split(':'); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
    x={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(x))); return x
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g[0],u))
def create_channel_row(c,w,name,private=False,topic=''):
    c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic)); i=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    owner=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(w,)).fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(i,owner)); return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def remove_channel_member(c,ch,u): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u))
def can_read_channel(u,ch):
    c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone(); c.close(); return bool(r)
def require_writable(ch):
    if ch['is_archived']: raise ValueError('archived')
def message_to_json(r,c=None):
    own = c is None
    c = c or connect()
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    reps=reactions_for(r['id'],c)
    n=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    fs=[file_to_json(x) for x in c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()]
    out={'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fs,'reactions':reps,'mentions':[]}
    if own: c.close()
    return out
def reactions_for(mid,c=None):
    own=c or connect(); rows=own.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)); d={}
    for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
    if not c: own.close()
    return [{'emoji':e,'count':len(u),'user_ids':u} for e,u in d.items()]
def create_message(user,ch,body,parent_id=None):
    with tx() as c:
        r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone(); require_writable(r)
        c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,user['id'],body,parent_id,now_iso())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); emit_event(c,ch,'message.reply' if parent_id else 'message.created',{'message':message_to_json(m,c)}); return message_to_json(m,c)
def react(user,mid,emoji,add=True):
    if not isinstance(emoji,str) or not emoji: raise ValueError()
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m: raise LookupError()
        ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
        if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],user['id'])).fetchone(): raise PermissionError()
        before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji)).fetchone()
        changed=(add and not before) or ((not add) and bool(before))
        if add:c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,user['id'],emoji))
        else:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji))
        if changed: emit_event(c,ch['id'],'reaction.added' if add else 'reaction.removed',{'message_id':mid,'user_id':user['id'],'emoji':emoji})
        return reactions_for(mid,c)
def file_to_json(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def create_group(c,w,u,handle,name,ids):
    c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w,handle,name,u)); gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    for x in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,x))
    return gid
def group_to_json(r):
    c=connect(); ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(r['id'],))]; c.close()
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
def update_profile(user,fields):
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    for k,v in fields.items():
        if k not in allowed or not isinstance(v,str): raise ValueError()
        if k=='timezone': ZoneInfo(v)
        if k=='display_name' and not 1<=len(v)<=80 or k=='status_text' and len(v)>140 or k=='status_emoji' and len(v)>32 or k=='avatar_url' and len(v)>512: raise ValueError()
    with tx() as c:
        for k,v in fields.items(): c.execute(f'UPDATE users SET {k}=? WHERE id=?',(v,user['id']))
        r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone(); emit_global(c,{'type':'user.updated','user':user_to_json(r)}); return r
def touch_presence(cid,uid):
    with tx() as c:c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(cid,uid,time.time()))
def drop_presence(cid):
    with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(cid,))
def present_user_ids():
    c=connect(); x=[r[0] for r in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))]; c.close(); return x
if __name__=='__main__':
    if '--init-db' in __import__('sys').argv:init_db()
