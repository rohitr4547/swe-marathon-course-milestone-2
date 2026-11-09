import sqlite3, os, hashlib, secrets, base64, json, re, time
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
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
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT, name TEXT, creator_id INTEGER, UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,created_by INTEGER,invited_user_id INTEGER,expires_at REAL,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
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
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:add_channel_member(c,g['id'],u)
def add_channel_member(c,ch,u):
    x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
    if x.rowcount:
        emit_event(c,ch,'member.joined',{'user_id':u})
    return bool(x.rowcount)
def resolve_mentions(c,body,channel,author_id):
    ids=set(); text=body or ''
    cands=re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',text)
    members=[r[0] for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],))]
    for name in cands:
        if name=='channel': ids.update(members)
        elif name=='here':
            ids.update([r[0] for r in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)) if r[0] in members])
        else:
            g=c.execute('SELECT id FROM groups WHERE workspace_id=? AND handle=?',(channel['workspace_id'],name)).fetchone()
            if g: ids.update([r[0] for r in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g[0],)) if r[0] in members])
            else:
                r=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
                if r and r[0] in members: ids.add(r[0])
    ids.discard(author_id); return sorted(ids)
def can_read_channel(user_id, channel):
    c=connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel['id'],user_id)).fetchone()
    if x: c.close(); return True
    if channel['is_private'] or not channel['workspace_id']: c.close(); return False
    x=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(channel['workspace_id'],user_id)).fetchone(); c.close()
    return bool(x and x['role']!='guest')
def require_writable(channel):
    return not bool(channel['is_archived'])
def create_channel_row(c,w,name,private=False,topic=''):
    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic)); return c.execute('SELECT * FROM channels WHERE id=?',(x.lastrowid,)).fetchone()
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
    d={'type':kind,'event_id':n,'channel_id':ch}; d.update(payload); c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(d))); return d
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(ch):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r[0] if r else 0
def events_after(ch,s):
    c=connect(); a=[json.loads(r[0]) for r in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,s))]; c.close(); return a
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4))); assert x['k']==k and isinstance(x['v'],int); return x['v']
    except: raise ValueError()
def parse_paging(q,k):
    n=int(q.get('limit','50')); assert 1<=n<=200
    return n,decode_cursor(k,q['cursor']) if q.get('cursor') else None
def message_to_json(r):
    c=connect(); u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rs=list(c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)))
    replies=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]; files=[file_to_json(f) for f in c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()]; c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies, 'created_at':r['created_at'],'edited_at':r['edited_at'],'files':files,'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs],'mentions':json.loads(r['mentions']) if 'mentions' in r.keys() and r['mentions'] else []}
if __name__=='__main__' and '--init-db' in __import__('sys').argv:init_db()

def reactions_for(mid):
 c=connect(); a=[]
 for r in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(mid,)): a.append({'emoji':r['emoji'],'count':r['n'],'user_ids':[int(x) for x in r['ids'].split(',')]})
 c.close(); return a

def open_dm(user, other_id):
    with tx() as c:
        other=c.execute('SELECT * FROM users WHERE id=?',(other_id,)).fetchone()
        if not other:return None
        a,b=sorted((user['id'],other_id)); old=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if old:return old['channel_id']
        x=c.execute("INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)",(f'dm-{a}-{b}',1))
        cid=x.lastrowid; c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid))
        c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,b))
        return cid

def save_file(user,filename,content_type,data):
    if len(data)>10*1024*1024: raise ValueError('large')
    with tx() as c:
        x=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(user['id'],filename,content_type,len(data),now_iso()))
        fid=x.lastrowid
        open(f'/app/data/files/{fid}','wb').write(data)
        return c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()

def file_to_json(r):
    return {'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}

def group_to_json(r):
 c=connect(); ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(r['id'],))]; c.close()
 return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}

def touch_presence(conn_id,user_id):
    with tx() as c: c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
    with tx() as c: c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))

def globals_after(since):
 c=connect(); a=[json.loads(r[0]) for r in c.execute('SELECT payload FROM global_events WHERE id>? ORDER BY id',(since,))]; c.close(); return a
