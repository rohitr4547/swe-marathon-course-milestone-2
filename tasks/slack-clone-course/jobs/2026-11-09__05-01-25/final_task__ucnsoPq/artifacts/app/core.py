import sqlite3, os, json, secrets, hashlib, base64, re, datetime, contextlib
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True)
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
@contextlib.contextmanager
def tx():
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
    except: c.execute('ROLLBACK'); raise
    finally: c.close()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "",created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b)); CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle)); CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id)); CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
    '''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
    if not t:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
    p={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(p))); return p
if __name__=='__main__':
    if '--init-db' in __import__('sys').argv:init_db()

def head_event_id(channel_id):
    c=connect(); r=c.execute('select last_event_id from channels where id=?',(channel_id,)).fetchone(); c.close(); return r[0] if r else 0
def events_after(channel_id,since):
    c=connect(); r=c.execute('select event_id,payload from events where channel_id=? and event_id>? order by event_id',(channel_id,since)).fetchall(); c.close(); return r

def reactions_for(message_id):
    c=connect(); rows=c.execute('select emoji,user_id from reactions where message_id=? order by rowid',(message_id,)).fetchall(); c.close()
    out=[]
    for r in rows:
        x=next((z for z in out if z['emoji']==r['emoji']),None)
        if not x:x={'emoji':r['emoji'],'count':0,'user_ids':[]};out.append(x)
        x['count']+=1;x['user_ids'].append(r['user_id'])
    return out

def group_to_json(r):
    c=connect(); ids=[x[0] for x in c.execute('select user_id from group_members where group_id=? order by user_id',(r['id'],)).fetchall()]; c.close()
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}

def resolve_mentions(c, body, channel, author_id):
    ids=set()
    for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)', body):
        if name in ('channel','here'): continue
        u=c.execute('select id from users where username=?',(name,)).fetchone()
        if u and u[0]!=author_id and c.execute('select 1 from channel_members where channel_id=? and user_id=?',(channel['id'],u[0])).fetchone(): ids.add(u[0])
    if '@channel' in body:
        ids.update(x[0] for x in c.execute('select user_id from channel_members where channel_id=? and user_id<>?',(channel['id'],author_id)).fetchall())
    if '@here' in body:
        try: ids.update(present_user_ids()-{author_id})
        except: pass
    for handle in re.findall(r'(?<![\w@])@([a-z0-9-]{1,32})',body):
        g=c.execute('select id from groups where workspace_id=? and handle=?',(channel['workspace_id'],handle)).fetchone() if channel['workspace_id'] else None
        if g:
            ids.update(x[0] for x in c.execute('select gm.user_id from group_members gm join channel_members cm on cm.user_id=gm.user_id where gm.group_id=? and cm.channel_id=? and gm.user_id<>?',(g[0],channel['id'],author_id)).fetchall())
    return list(ids-{author_id})
def present_user_ids():
    c=connect(); 
    try: return {x[0] for x in c.execute('select user_id from presence where last_seen>?',(datetime.datetime.now().timestamp()-3,)).fetchall()}
    except: return set()

def emit_global(c,payload):
    c.execute('insert into global_events(payload) values(?)',(json.dumps(payload),))
def globals_after(since):
    c=connect(); r=c.execute('select id,payload from global_events where id>? order by id',(since,)).fetchall(); c.close(); return r
def update_profile(user, fields):
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if any(k not in allowed or not isinstance(v,str) for k,v in fields.items()): raise ValueError('invalid profile')
    if 'display_name' in fields and not 1<=len(fields['display_name'])<=80: raise ValueError('invalid display_name')
    if 'status_text' in fields and len(fields['status_text'])>140: raise ValueError('invalid status_text')
    if 'status_emoji' in fields and len(fields['status_emoji'])>32: raise ValueError('invalid status_emoji')
    if 'avatar_url' in fields and len(fields['avatar_url'])>512: raise ValueError('invalid avatar_url')
    if 'timezone' in fields:
        from zoneinfo import ZoneInfo
        try: ZoneInfo(fields['timezone'])
        except: raise ValueError('invalid timezone')
    with tx() as c:
        if fields:
            c.execute('update users set '+','.join(k+'=?' for k in fields)+' where id=?',tuple(fields.values())+(user['id'],))
        row=c.execute('select * from users where id=?',(user['id'],)).fetchone()
        emit_global(c,{'type':'user.updated','user':user_to_json(row)})
    return row
