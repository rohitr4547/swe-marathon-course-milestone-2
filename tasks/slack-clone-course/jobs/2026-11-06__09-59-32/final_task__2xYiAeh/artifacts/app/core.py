import sqlite3, os, json, hashlib, secrets, base64, re, datetime, sys, time
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class Tx:
    def __enter__(self): self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def tx(): return Tx()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
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
def get_channel(cid):
    c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();c.close();return r
def member_role(wid,uid):
    c=connect();r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone();c.close();return r['role'] if r else None
def add_channel_member(c,cid,uid):
    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid))
def remove_channel_member(c,cid,uid): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid))
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g:add_channel_member(c,g['id'],uid)
def create_channel_row(c,wid,name,private=False,topic=''):
    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic)); return x.lastrowid
def join_channel(user,cid):
    ch=get_channel(cid)
    if not ch:return False,'not found'
    with tx() as c:
        if ch['workspace_id']:
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],user['id'])).fetchone():
                if w['join_mode']!='open' or ch['is_private']: return False,'forbidden'
                add_workspace_member(c,w['id'],user['id'])
        if ch['is_private'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,user['id'])).fetchone(): return False,'forbidden'
        add_channel_member(c,cid,user['id'])
    return True,None
def can_read_channel(uid,ch):
    c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close(); return bool(r)
def require_writable(ch): return not ch['is_archived']
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':cid,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return frame
def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(cid):
    c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone();c.close();return r['last_event_id'] if r else 0
def events_after(cid,since):
    c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)); out=[json.loads(x['payload']) for x in r];c.close();return out
def encode_cursor(kind,v): return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':int(v)}).encode()).decode().rstrip('=')
def decode_cursor(kind,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); assert x.get('k')==kind and isinstance(x.get('v'),int); return x['v']
    except: raise ValueError()
def parse_paging(q,kind):
    try:
        lim=int(q.get('limit','50')); assert 1<=lim<=200
        cur=decode_cursor(kind,q['cursor']) if q.get('cursor') else None; return lim,cur
    except: raise ValueError()
def resolve_mentions(c,body,ch,author):
    out=[]
    names=re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body)
    if 'channel' in names:
        rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],))
        out += [r['user_id'] for r in rows if r['user_id']!=author]
    for n in names:
        r=c.execute('SELECT u.id FROM users u JOIN members m ON m.user_id=u.id WHERE u.username=? AND m.workspace_id=?',(n,ch['workspace_id'])) .fetchone() if ch['workspace_id'] else None
        if r and r['id']!=author: out.append(r['id'])
    return list(dict.fromkeys(out))
def reactions_for(mid,c=None):
    own=c is None
    if own: c=connect()
    rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)); d={}
    for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
    if own: c.close()
    return [{'emoji':e,'count':len(u),'user_ids':u} for e,u in d.items()]
def message_to_json(r,c=None):
    if not r:return None
    own=c is None
    if own: c=connect()
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); n=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    if own: c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions_for(r['id'],c if not own else None),'mentions':json.loads(r['mentions'] or '[]')}
def create_message(user,cid,body,parent_id=None):
    ch=get_channel(cid)
    with tx() as c:
        m=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,user['id'])).fetchone()
        if not m: raise PermissionError()
        if not require_writable(ch): raise RuntimeError()
        ment=resolve_mentions(c,body,ch,user['id']); now=now_iso()
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,user['id'],body,parent_id,now,json.dumps(ment))); mid=cur.lastrowid
        msg=message_to_json(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(),c)
        emit_event(c,cid,'message.reply' if parent_id else 'message.created',{'message':msg})
    return msg
def list_messages(cid,limit=50,cursor=None):
    c=connect(); q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'; args=[cid]
    if cursor is not None:q+=' AND id<?';args.append(cursor)
    q+=' ORDER BY id DESC LIMIT ?';args.append(limit+1); rows=c.execute(q,args).fetchall();c.close()
    more=len(rows)>limit; rows=rows[:limit];return [message_to_json(x) for x in rows],(encode_cursor('messages',rows[-1]['id']) if more and rows else None)
def list_replies(pid,limit=50,cursor=None):
    c=connect();q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';a=[pid]
    if cursor is not None:q+=' AND id>?';a.append(cursor)
    q+=' ORDER BY id ASC LIMIT ?';a.append(limit+1);rs=c.execute(q,a).fetchall();c.close(); more=len(rs)>limit;rs=rs[:limit]
    return [message_to_json(x) for x in rs],(encode_cursor('replies',rs[-1]['id']) if more and rs else None)
def edit_message(user,mid,body):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r: raise LookupError()
        if r['deleted']: raise RuntimeError()
        if r['author_id']!=user['id']: raise PermissionError()
        ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone(); ment=resolve_mentions(c,body,ch,user['id']); t=now_iso()
        c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(body,t,json.dumps(ment),mid)); msg=message_to_json(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(),c);emit_event(c,r['channel_id'],'message.updated',{'message':msg})
    return msg
def delete_message(user,mid):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r: raise LookupError()
        if r['deleted']: return
        ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone(); role=(c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],user['id'])).fetchone() or {'role':None})['role'] if ch['workspace_id'] else None
        if r['author_id']!=user['id'] and role!='owner':raise PermissionError()
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid})
def react(user,mid,emoji,add=True):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r or r['deleted']:raise LookupError()
        if add:c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,user['id'],emoji))
        else:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji))
        emit_event(c,r['channel_id'],'reaction.added' if add else 'reaction.removed',{'message_id':mid,'emoji':emoji})
    return reactions_for(mid)
def open_dm(user,other):
    a,b=sorted((user['id'],other))
    with tx() as c:
        r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if r:return r['channel_id']
        cur=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)');cid=cur.lastrowid;c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));add_channel_member(c,cid,a);add_channel_member(c,cid,b);return cid
if __name__=='__main__':
    if '--init-db' in sys.argv:init_db()
