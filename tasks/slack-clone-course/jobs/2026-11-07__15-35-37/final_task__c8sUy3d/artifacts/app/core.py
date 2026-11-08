import sqlite3, os, json, hashlib, secrets, base64, re, argparse, time
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
class Tx:
    def __enter__(self):
        self.c=connect();self.c.execute('BEGIN IMMEDIATE');return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT');self.c.close()
def tx(): return Tx()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    os.makedirs('/app/data/files',exist_ok=True);c=connect();c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    ''');c.close()
def hash_password(pw):
    s=secrets.token_hex(16);return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:s,h=stored.split('$',1);return hashlib.sha256((s+pw).encode()).hexdigest()==h
    except:return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));return t
def user_for_token(t):
    if not t:return None
    c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone();c.close();return r
def user_to_json(r):
    return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def workspace_to_json(r): return {k:r[k] for k in ['id','slug','name','owner_id','join_mode']}
def channel_to_json(r):
    return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ['id','workspace_id','name','is_private','is_dm','topic','is_archived']}
def get_workspace(slug):
    c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def get_channel(i):
    c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def member_role(w,u):
    c=connect();r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u))
def add_channel_member(c,ch,u):
    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
    emit_event(c,ch,'member.joined',{'user_id':u})
def remove_channel_member(c,ch,u): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u))
def create_channel_row(c,w,name,private=False,topic=''):
    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic));return c.execute('SELECT * FROM channels WHERE id=?',(x.lastrowid,)).fetchone()
def join_channel(user,chid):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(chid,)).fetchone()
        if not ch:return None
        if ch['workspace_id']:
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            add_workspace_member(c,w['id'],user['id'],'member')
        add_channel_member(c,chid,user['id'])
        return ch
def can_read_channel(uid,ch):
    c=connect();r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone();c.close();return bool(r)
def require_writable(ch): return not ch['is_archived']
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame)));return frame
def head_event_id(ch):
    c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return r['last_event_id'] if r else 0
def events_after(ch,since):
    c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since));a=[json.loads(x[0]) for x in r];c.close();return a
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        o=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4)));assert isinstance(o,dict) and o['k']==k and isinstance(o['v'],int);return o['v']
    except: raise ValueError()
def parse_paging(q,k):
    try:n=int(q.get('limit','50'));assert 1<=n<=200
    except:raise ValueError()
    cur=q.get('cursor');return n,(decode_cursor(k,cur) if cur else None)
def resolve_mentions(c,body,ch,author):
    out=[]
    if '@channel' in body:
        out += [x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id<>?',(ch['id'],author))]
    names=set(re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body))
    for n in names:
        if n=='channel':continue
        r=c.execute('SELECT u.id FROM users u JOIN workspace_members m ON m.user_id=u.id WHERE u.username=? AND m.workspace_id=? AND u.id<>?',(n,ch['workspace_id'],author)).fetchone()
        if r:out.append(r['id'])
    return list(dict.fromkeys(out))
def msg_json(c,r):
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    reps=[]
    for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
        reps.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]})
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reps,'mentions':[]}
def create_message(user,chid,body,parent=None):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(chid,)).fetchone()
        if not ch or not body or not body.strip(): raise ValueError('invalid')
        if ch['is_archived']: raise RuntimeError()
        if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(chid,user['id'])).fetchone(): raise PermissionError()
        ts=now_iso();x=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(chid,user['id'],body,parent,ts));r=c.execute('SELECT * FROM messages WHERE id=?',(x.lastrowid,)).fetchone()
        mentions=resolve_mentions(c,body,ch,user['id']);kind='message.reply' if parent else 'message.created';frame=emit_event(c,chid,kind,{'message':msg_json(c,r)})
        return msg_json(c,r),frame
def list_messages(chid,limit,cursor):
    c=connect();q='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0';p=[chid]
    if cursor is not None:q+=' AND id<?';p.append(cursor)
    q+=' ORDER BY id DESC LIMIT ?';p.append(limit);rs=list(c.execute(q,p));a=[msg_json(c,r) for r in rs];n=encode_cursor('messages',rs[-1]['id']) if len(rs)==limit else None;c.close();return a,n
def list_replies(pid,limit,cursor):
    c=connect();q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';p=[pid]
    if cursor is not None:q+=' AND id>?';p.append(cursor)
    q+=' ORDER BY id LIMIT ?';p.append(limit);rs=list(c.execute(q,p));a=[msg_json(c,r) for r in rs];n=encode_cursor('replies',rs[-1]['id']) if len(rs)==limit else None;c.close();return a,n
def reactions_for(mid):
    c=connect();a=[]
    for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)):a.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]})
    c.close();return a
def react(user,mid,emoji,add=True):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r:raise LookupError()
        if add:c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,user['id'],emoji));kind='reaction.added'
        else:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji));kind='reaction.removed'
        f=emit_event(c,r['channel_id'],kind,{'message_id':mid,'emoji':emoji});return reactions_for(mid),f
def edit_message(user,mid,body):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r or r['author_id']!=user['id']:raise PermissionError()
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,now_iso(),mid));r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();return msg_json(c,r)
def delete_message(user,mid):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r:return True
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid});return True
def open_dm(user,other):
    a,b=sorted((user['id'],other))
    with tx() as c:
        r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if r:return r['channel_id']
        x=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)');cid=x.lastrowid;c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,b));return cid
def touch_presence(i,u):
    c=connect();c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(i,u,time.time()));c.close()
def drop_presence(i):
    c=connect();c.execute('DELETE FROM presence WHERE conn_id=?',(i,));c.close()
def present_user_ids():
    c=connect();a=[r[0] for r in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))];c.close();return a
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--init-db',action='store_true')
    if ap.parse_args().init_db:init_db()
