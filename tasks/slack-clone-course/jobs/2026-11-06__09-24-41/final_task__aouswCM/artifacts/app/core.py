import sqlite3, os, json, time, hashlib, secrets, base64, re
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
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE INDEX IF NOT EXISTS mi ON messages(channel_id,id);
    '''); c.close()
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p,s): return secrets.compare_digest(hash_password(p),s)
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
    if not t:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    if not r:return None
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}

def workspace_to_json(r): return dict(r)
def channel_to_json(r):
    d=dict(r); d['is_private']=bool(d['is_private']); d['is_dm']=bool(d['is_dm']); d['is_archived']=bool(d['is_archived']); return d
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
    g=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(w,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u))
def create_channel_row(c,w,name,private=False,topic=''):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic)); return c.execute('SELECT * FROM channels WHERE id=?',(cur.lastrowid,)).fetchone()
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def remove_channel_member(c,ch,u): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u))
def can_read_channel(u,ch):
    c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone(); c.close(); return bool(r)
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def head_event_id(ch):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r['last_event_id'] if r else 0
def events_after(ch,since):
    c=connect(); x=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall(); c.close(); return [json.loads(r[0]) for r in x]
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); assert x['k']==k and isinstance(x['v'],int); return x['v']
    except: raise ValueError()
def parse_paging(q,k):
    try:
        n=int(q.get('limit','50')); assert 1<=n<=200
        cur=decode_cursor(k,q['cursor']) if q.get('cursor') else None; return n,cur
    except: raise ValueError()
def message_to_json(r):
    d=dict(r); d['author']=user_to_json(r['_author']) if '_author' in r.keys() else None; d['parent_id']=d['parent_id']; d['reply_count']=0; d['files']=[]; d['reactions']=[]; d['mentions']=[]; return d
def create_message(user,ch,body,parent=None):
    with tx() as c:
        t=now_iso(); x=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,user,body,parent,t)).lastrowid
        r=c.execute('SELECT * FROM messages WHERE id=?',(x,)).fetchone(); a=c.execute('SELECT * FROM users WHERE id=?',(user,)).fetchone(); d=dict(r); d['author']=user_to_json(a)
        names=re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',body)
        members=c.execute('SELECT u.id,u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch,)).fetchall()
        byname={x['username']:x['id'] for x in members}; mentions=[]
        for n in names:
            ids=[x['id'] for x in members] if n=='channel' else ([x['id'] for x in members if x['username']==n] if n!='here' else [])
            for mid in ids:
                if mid!=user and mid not in mentions: mentions.append(mid)
        d.update(reply_count=0,files=[],reactions=[],mentions=mentions)
        kind='message.reply' if parent else 'message.created'; f=emit_event(c,ch,kind,{'message':d}); return d
def list_messages(ch,limit,cursor):
    c=connect(); q='SELECT m.*,u.username author_name,u.display_name FROM messages m JOIN users u ON u.id=m.author_id WHERE m.channel_id=? AND m.deleted=0 AND m.parent_id IS NULL'; args=[ch]
    if cursor:q+=' AND m.id<?';args.append(cursor)
    q+=' ORDER BY m.id DESC LIMIT ?';args.append(limit+1); rows=c.execute(q,args).fetchall(); c.close()
    out=[]; 
    for r in rows[:limit]:
        d=dict(r); d['author']={'id':d['author_id'],'username':d['author_name'],'display_name':d['display_name']}; d['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(d['id'],)).fetchone()[0] if False else 0; d.update(files=[],reactions=[],mentions=[]); out.append(d)
    return out,encode_cursor('messages',rows[limit-1]['id']) if len(rows)>limit else None
def join_channel(u,chid):
    ch=get_channel(chid)
    if not ch:return None
    with tx() as c:
        if ch['workspace_id'] and not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u)).fetchone():
            add_workspace_member(c,ch['workspace_id'],u)
        add_channel_member(c,chid,u)
    return ch
if __name__=='__main__':
    if '--init-db' in __import__('sys').argv:init_db()

def reactions_for(mid):
    c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji',(mid,)).fetchall(); c.close()
    out={}
    for r in rows: out.setdefault(r['emoji'],[]).append(r['user_id'])
    return [{'emoji':e,'count':len(ids),'user_ids':ids} for e,ids in out.items()]
def react(user,mid,emoji,add=True):
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m:return None
        if add:c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,user,emoji)); kind='reaction.added'
        else:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user,emoji)); kind='reaction.removed'
        emit_event(c,m['channel_id'],kind,{'message_id':mid,'emoji':emoji})
    return reactions_for(mid)
def edit_message(user,mid,body):
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m:return None,'not found'
        if m['author_id']!=user:return None,'forbidden'
        if m['deleted']:return None,'deleted'
        t=now_iso(); c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,t,mid))
        d=dict(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); d['author']=dict(c.execute('SELECT * FROM users WHERE id=?',(user,)).fetchone()); d.update(reply_count=0,files=[],reactions=reactions_for(mid),mentions=[])
        emit_event(c,m['channel_id'],'message.updated',{'message':d}); return d,None
def delete_message(user,mid):
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m:return False,'not found'
        if m['author_id']!=user:return False,'forbidden'
        if not m['deleted']:
            c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
    return True,None
