import sqlite3, os, hashlib, secrets, json, base64, re
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT UNIQUE,name TEXT,creator_id INTEGER); CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id)); CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL,channel_id INTEGER);
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at REAL,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
    '''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try: s,h=stored.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?,?)',(t,uid,now_iso())); return t
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
    c=connect();r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def create_channel_row(c,w,name,private=False,topic=''):
    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic)); return x.lastrowid
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:add_channel_member(c,g[0],u)
def message_to_json(r):
    c=connect(); a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); reply_count=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    matches=list(re.finditer(r'(?<![A-Za-z0-9_@])@([A-Za-z0-9_-]+)',r['body']))
    names=[]
    for mt in matches:
        before=r['body'][:mt.start()].split()[-1] if r['body'][:mt.start()].split() else ''
        after=r['body'][mt.end():]
        if after.startswith('@') or (before and '@' in before): continue
        if before and ('.' in before or '@' in before): continue
        names.append(mt.group(1))
    mention_rows=[]
    for name in names:
        z=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
        if z and z['id'] != r['author_id'] and z['id'] not in [x['id'] for x in mention_rows]:
            allowed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],z['id'])).fetchone()
            if allowed: mention_rows.append(z)
    mentions=[x['id'] for x in mention_rows]
    for name in names:
        gr=c.execute('SELECT id FROM groups WHERE handle=?',(name,)).fetchone()
        if gr:
            gm=c.execute('SELECT user_id FROM group_members WHERE group_id=?',(gr['id'],)).fetchall()
            cm={x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(r['channel_id'],)).fetchall()}
            mentions.extend(x['user_id'] for x in gm if x['user_id'] in cm and x['user_id'] != r['author_id'])
    if 'here' in names:
        active=c.execute('SELECT DISTINCT p.user_id FROM presence p JOIN channel_members cm ON cm.user_id=p.user_id WHERE cm.channel_id=? AND p.last_seen>?',(r['channel_id'],__import__('time').time()-3)).fetchall()
        mentions.extend(x['user_id'] for x in active if x['user_id'] != r['author_id'])
    if 'channel' in names:
        cm=c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(r['channel_id'],)).fetchall()
        mentions.extend(x['user_id'] for x in cm if x['user_id'] != r['author_id'])
    mentions=list(dict.fromkeys(mentions))
    files=[dict(x) for x in c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE message_id=?',(r['id'],)).fetchall()]
    c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reply_count,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':files,'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs],'mentions':mentions}
def create_message(user,ch,body,parent_id=None):
    with tx() as c:
        r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
        if not r:return None
        if int(r['is_archived'] or 0) != 0: raise ValueError('archived')
        x=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,user['id'],body,parent_id,now_iso())); m=c.execute('SELECT * FROM messages WHERE id=?',(x.lastrowid,)).fetchone()
        emit_event(c,ch,'message.reply' if parent_id else 'message.created',{'message':message_to_json(m)})
    return message_to_json(m)
if __name__=='__main__': init_db()
