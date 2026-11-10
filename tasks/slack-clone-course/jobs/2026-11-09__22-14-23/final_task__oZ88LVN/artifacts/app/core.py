import sqlite3, os, json, hashlib, secrets, re, base64
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def conn():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    os.makedirs('/app/data',exist_ok=True); c=conn(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS members(user_id INTEGER,channel_id INTEGER,role TEXT DEFAULT 'member',PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS wmembers(user_id INTEGER,workspace_id INTEGER,role TEXT DEFAULT 'member',PRIMARY KEY(user_id,workspace_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    '''); c.close()
def user_json(r):
    return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def ws_json(r): return {k:r[k] for k in ['id','slug','name','owner_id','join_mode']}
def ch_json(r): return {k:r[k] for k in ['id','workspace_id','name','is_private','is_dm','topic','is_archived']}
def pw(x): return hashlib.sha256(x.encode()).hexdigest()
def add_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO wmembers VALUES(?,?,?)',(u,w,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(u,g['id'],role))
def emit(c,cid,typ,payload):
    c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(cid,))
    n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()[0]
    frame={'type':typ,'event_id':n,'channel_id':cid,**payload}
    c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame)))
    return frame
def msg_json(r,c=None):
    d=dict(r); d['author']=user_json(c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()) if c else None
    d['parent_id']=r['parent_id']; d['reply_count']=0; d['files']=[]; d['reactions']=[]; d['mentions']=json.loads(r['mentions'] or '[]')
    if c:
        d['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
        d['reactions']=[{'emoji':x['emoji'],'count':x['count'],'user_ids':[]} for x in c.execute('SELECT emoji,count(*) AS count FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()]
    return d
if __name__=='__main__':
    init_db()
