import sqlite3, os, re, json, base64, hashlib, secrets, datetime, argparse, time
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True)
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
class tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    stmts=[
    'CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "",created_at TEXT)',
    'CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT)',
    'CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open")',
    'CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id))',
    'CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0)',
    'CREATE UNIQUE INDEX IF NOT EXISTS channel_names ON channels(workspace_id,name)',
    'CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id))',
    'CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0)',
    'CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id))',
    'CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT)',
    'CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji))',
    'CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT)',
    'CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b))',
    'CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER)',
    'CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER)',
    'CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id))',
    'CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,creator_id INTEGER,expires_at REAL,max_uses INTEGER,uses INTEGER DEFAULT 0,invited_user_id INTEGER)',
    'CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id))',
    'CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL)']
    for s in stmts:c.execute(s)
    c.close()
def hash_password(pw):
    salt=secrets.token_hex(16); return salt+'$'+hashlib.sha256((salt+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
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
    c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def get_channel(i):
    c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):
    c=connect();r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w,u,role))
    g=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(w,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u))
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def remove_channel_member(c,ch,u): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u))
def create_channel_row(c,w,name,private=False,topic=''):
    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic)); return x.lastrowid
def can_read_channel(u,ch):
    c=connect()
    r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone()
    if not r and ch['workspace_id'] and not ch['is_private']:
        r=c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u)).fetchone()
    c.close()
    return bool(r)
def require_writable(ch):
    if ch and ch['is_archived']: raise ValueError('archived')
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':ch};frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame)));return frame
def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(ch):
    c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return r[0] if r else 0
def events_after(ch,since):
    c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall();c.close();return [json.loads(x[0]) for x in r]
def globals_after(since):
    c=connect();r=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall();c.close();return [(x[0],json.loads(x[1])) for x in r]
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4))); assert x['k']==k and isinstance(x['v'],int); return x['v']
    except: raise ValueError('invalid cursor')
def parse_paging(q,k):
    if 'page' in q or 'offset' in q:
        raise ValueError('invalid pagination')
    try:
        n=int(q.get('limit','50')); assert 1<=n<=200
        return n,decode_cursor(k,q['cursor']) if q.get('cursor') else None
    except: raise ValueError('invalid pagination')
def resolve_mentions(c,body,channel,author_id):
    ids=[]
    members=[x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],)).fetchall()]
    if '@channel' in body: ids += [x for x in members if x != author_id]
    for m in re.finditer(r'(?<![\w@])@([A-Za-z0-9_-]+)',body):
        name=m.group(1)
        if name=='channel': continue
        if name=='here':
            ids += [x[0] for x in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall() if x[0] in members and x[0]!=author_id]
            continue
        g=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(channel['workspace_id'],name)).fetchone() if channel['workspace_id'] else None
        if g:
            ids += [x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall() if x[0] in members and x[0]!=author_id]
            continue
        u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
        if u and u['id'] in members and u['id'] != author_id: ids.append(u['id'])
    return list(dict.fromkeys(ids))
def message_to_json(r):
    c=connect();a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    reps=[]
    files=[]
    for f in c.execute('SELECT * FROM files WHERE message_id=? ORDER BY id',(r['id'],)):
        files.append({'id':f['id'],'uploader_id':f['uploader_id'],'filename':f['filename'],'content_type':f['content_type'],'size':f['size'],'created_at':f['created_at']})
    channel=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    mentions=resolve_mentions(c,r['body'],channel,r['author_id']) if channel else []
    for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
        reps.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]})
    replies=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0];c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':files,'reactions':reps,'mentions':mentions}
def create_message(user,ch,body,parent_id=None):
    if not isinstance(body,str) or not body.strip(): raise ValueError('body')
    with tx() as c:
        r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone();require_writable(r)
        t=now_iso();cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,user['id'],body,parent_id,t));mid=cur.lastrowid
        kind='message.reply' if parent_id else 'message.created';emit_event(c,ch,kind,{'message':message_to_json(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})
    return message_to_json(connect().execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())
def list_messages(ch,limit=50,cursor=None):
    c=connect(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 AND id<? ORDER BY id DESC LIMIT ?', (ch,cursor or 10**18,limit+1)).fetchall();c.close()
    more=len(rows)>limit;rows=rows[:limit];return [message_to_json(x) for x in rows],(encode_cursor('messages',rows[-1]['id']) if more and rows else None)
def list_replies(ch,limit=50,cursor=None):
    c=connect();rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id LIMIT ?',(ch,cursor or 0,limit+1)).fetchall();c.close();more=len(rows)>limit;rows=rows[:limit];return [message_to_json(x) for x in rows],(encode_cursor('replies',rows[-1]['id']) if more and rows else None)
def reactions_for(mid):
    c=connect();r=[{'emoji':x[0],'count':x[1],'user_ids':[int(i) for i in x[2].split(',')]} for x in c.execute('SELECT emoji,COUNT(*),GROUP_CONCAT(user_id) FROM reactions WHERE message_id=? GROUP BY emoji',(mid,))];c.close();return r

if __name__=='__main__':
    if '--init-db' in __import__('sys').argv: init_db()
def touch_presence(conn_id,user_id):
    with tx() as c:c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
    with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
