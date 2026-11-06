import sqlite3, hashlib, secrets, json, base64, re, datetime, os, time, argparse
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
class Tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def tx(): return Tx()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE INDEX IF NOT EXISTS msg_chan ON messages(channel_id,id);
    '''); c.close()
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
    c=connect();r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:add_channel_member(c,g['id'],u)
def create_channel_row(c,w,name,private=False,topic=''):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic));return c.execute('SELECT * FROM channels WHERE id=?',(cur.lastrowid,)).fetchone()
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame)));return frame
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(ch):
    c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return r['last_event_id'] if r else 0
def events_after(ch,since):
    c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since));x=[json.loads(a[0]) for a in r];c.close();return x
def parse_paging(q,kind):
    try:
        lim=int(q.get('limit','50')); assert 1<=lim<=200
        cur=q.get('cursor'); v=None
        if cur:v=decode_cursor(kind,cur)
        return lim,v
    except: raise ValueError()
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        d=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4)));assert isinstance(d,dict) and d['k']==k and isinstance(d['v'],int);return d['v']
    except: raise ValueError()
def can_read_channel(u,ch):
    c=connect();x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone();c.close();return bool(x)
def require_writable(ch): return bool(ch and not ch['is_archived'])
def resolve_mentions(c,body,ch,author):
    out=set()
    for name in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
        if name=='channel':
            out.update(x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],author)))
        else:
            x=c.execute('SELECT u.id FROM users u JOIN workspace_members m ON m.user_id=u.id WHERE u.username=? AND m.workspace_id=? AND u.id!=?',(name,ch['workspace_id'],author)).fetchone()
            if x:out.add(x[0])
    return list(out)
def reactions_for(message_id,c=None):
    own=c is None
    if own: c=connect()
    rs=c.execute("SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id",(message_id,)).fetchall()
    if own: c.close()
    out={}
    for x in rs: out.setdefault(x['emoji'],[]).append(x['user_id'])
    return [{'emoji':e,'count':len(ids),'user_ids':ids} for e,ids in out.items()]
def react(user,message_id,emoji,add=True):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(message_id,)).fetchone()
        if not r: raise ValueError('not found')
        if add: c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(message_id,user['id'],emoji))
        else: c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user['id'],emoji))
        out=reactions_for(message_id,c)
        emit_event(c,r['channel_id'],'reaction.added' if add else 'reaction.removed',{'message_id':message_id,'emoji':emoji,'reactions':out})
        return out
def message_json(c,r):
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    replies=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    mentions=resolve_mentions(c,r['body'],ch,r['author_id']) if ch else []
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions_for(r['id']),'mentions':mentions}
def create_message(user,ch,body,parent=None):
    with tx() as c:
        row=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone(); now=now_iso()
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,user['id'],body,parent,now))
        r=c.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone(); m=message_json(c,r);m['mentions']=resolve_mentions(c,body,row,user['id'])
        emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':m})
        return m
def list_messages(ch,limit,cursor=None):
    c=connect(); q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'
    args=[ch]
    if cursor is not None:q+=' AND id<?';args.append(cursor)
    q+=' ORDER BY id DESC LIMIT ?';args.append(limit+1);rs=c.execute(q,args).fetchall(); out=[message_json(c,r) for r in rs[:limit]]; nxt=encode_cursor('messages',rs[limit-1]['id']) if len(rs)>limit else None;c.close();return out,nxt
def list_replies(pid,limit,cursor=None):
    c=connect();q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';a=[pid]
    if cursor is not None:q+=' AND id>?';a.append(cursor)
    q+=' ORDER BY id LIMIT ?';a.append(limit+1);rs=c.execute(q,a).fetchall();out=[message_json(c,r) for r in rs[:limit]];n=encode_cursor('replies',rs[limit-1]['id']) if len(rs)>limit else None;c.close();return out,n
def touch_presence(i,u): 
    c=connect();c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(i,u,time.time()));c.close()
def drop_presence(i): c=connect();c.execute('DELETE FROM presence WHERE conn_id=?',(i,));c.close()
def present_user_ids():
    c=connect();r=[x[0] for x in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))];c.close();return r
if __name__=='__main__':
    init_db()
