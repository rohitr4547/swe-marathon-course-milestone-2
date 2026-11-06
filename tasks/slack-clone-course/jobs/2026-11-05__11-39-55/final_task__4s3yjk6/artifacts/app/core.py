import sqlite3, os, json, hashlib, secrets, base64, re, datetime, time, argparse
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class Tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def tx(): return Tx()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    os.makedirs('/app/data/files',exist_ok=True); c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open',created_at TEXT);
    CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]',event_id INTEGER);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT,UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY,workspace_id INTEGER,code TEXT UNIQUE,expires_at REAL,max_uses INTEGER,uses INTEGER DEFAULT 0,invited_user_id INTEGER,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    '''); c.close()
def hash_password(pw):
    salt=secrets.token_hex(16); return salt+'$'+hashlib.sha256((salt+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try: s,h=stored.split('$',1); return hashlib.sha256((s+pw).encode()).hexdigest()==h
    except: return False
def issue_token(c,user_id):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,user_id)); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def channel_to_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def add_channel_member(c,cid,uid): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid))
def remove_channel_member(c,cid,uid): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid))
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g:add_channel_member(c,g['id'],uid)
def create_channel_row(c,wid,name,private=False,topic=''):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic)); return cur.lastrowid
def can_read_channel(uid,ch):
    c=connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone()
    if not x and not ch['is_private'] and ch['workspace_id']:
        x=c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],uid)).fetchone()
    c.close(); return bool(x)
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':cid,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return frame
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def latest_global_id():
    c=connect();r=c.execute('SELECT COALESCE(MAX(id),0) v FROM global_events').fetchone();c.close();return r['v']
def globals_after(since):
    c=connect();out=[]
    for r in c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)):
        x=json.loads(r['payload']);x['_global_id']=r['id'];out.append(x)
    c.close();return out
def head_event_id(cid):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r['last_event_id'] if r else 0
def events_after(cid,since):
    c=connect(); a=[json.loads(r['payload']) for r in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since))]; c.close(); return a
def parse_paging(q,kind):
    lim=int(q.get('limit','50')); 
    if lim<1 or lim>200: raise ValueError()
    cur=q.get('cursor'); v=None
    if cur:
        try:
            raw=base64.urlsafe_b64decode(cur+'='*(-len(cur)%4)); x=json.loads(raw)
            if x.get('k')!=kind or not isinstance(x.get('v'),int): raise ValueError()
            v=x['v']
        except: raise ValueError()
    return lim,v
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def resolve_mentions(c,body,ch,author):
    out=[]
    names=re.findall(r'(?<![\w@.])@([\w-]+)',body)
    for name in names:
        if name=='channel':
            rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],))
            ids=[r['user_id'] for r in rows]
        elif name=='here':
            rows=c.execute("SELECT DISTINCT user_id FROM presence WHERE last_seen>?",(time.time()-3,))
            present={r['user_id'] for r in rows}
            ids=[r['user_id'] for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)) if r['user_id'] in present]
        else:
            gr=c.execute('SELECT user_ids FROM groups_ WHERE workspace_id=? AND handle=?',(ch['workspace_id'],name)).fetchone() if ch['workspace_id'] else None
            if gr:
                wanted=set(json.loads(gr['user_ids'] or '[]'))
                ids=[r['user_id'] for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)) if r['user_id'] in wanted]
            else:
                rows=c.execute('SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.workspace_id=? AND u.username=?',(ch['workspace_id'],name))
                ids=[r['id'] for r in rows]
        for uid in ids:
            if uid!=author and uid not in out: out.append(uid)
    return out
def message_to_json(r):
    c=connect(); u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); reps=[]
    for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids,MIN(rowid) first_row FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY first_row',(r['id'],)): reps.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]})
    rc=c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    files=[]
    for f in c.execute('SELECT * FROM files WHERE message_id=? ORDER BY id',(r['id'],)):
        files.append({'id':f['id'],'uploader_id':f['uploader_id'],'filename':f['filename'],'content_type':f['content_type'],'size':f['size'],'created_at':f['created_at']})
    c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':files,'reactions':reps,'mentions':json.loads(r['mentions'] or '[]')}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--init-db',action='store_true')
    args=parser.parse_args()
    if args.init_db:
        init_db()

def touch_presence(conn_id,user_id):
    with tx() as c:c.execute('INSERT INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?) ON CONFLICT(conn_id) DO UPDATE SET last_seen=excluded.last_seen',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
    with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
def present_user_ids():
    c=connect();a=[r['user_id'] for r in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))];c.close();return a
