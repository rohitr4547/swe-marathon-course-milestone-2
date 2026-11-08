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
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    '''); c.close()
def hash_password(pw):
    salt=secrets.token_hex(16); return salt+':'+hashlib.sha256((salt+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try: s,h=stored.split(':',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r): return {k:r[k] for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],uid))
def add_channel_member(c,cid,uid): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid))
def remove_channel_member(c,cid,uid): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid))
def can_read_channel(uid,ch):
    c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close(); return bool(r)
def require_writable(ch):
    return bool(ch) and not ch['is_archived']
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':cid}; frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return frame
def head_event_id(cid):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r[0] if r else 0
def events_after(cid,since):
    c=connect(); a=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); c.close(); return [json.loads(x[0]) for x in a]
def encode_cursor(kind,v): return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(kind,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4))); assert x['k']==kind and isinstance(x['v'],int); return x['v']
    except: raise ValueError()
def create_channel_row(c,wid,name,private=False,topic=''):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic)); return c.execute('SELECT * FROM channels WHERE id=?',(cur.lastrowid,)).fetchone()
def resolve_mentions(c,body,ch,author):
    out=[]
    for u in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
        if u=='channel':
            ids=[x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],author))]
        else:
            ids=[x[0] for x in c.execute('SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.workspace_id=? AND u.username=? AND u.id!=?',(ch['workspace_id'],u,author))]
        out += ids
    return list(dict.fromkeys(out))
def mention_ids(body,cid,author):
    c=connect(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch:
        c.close(); return []
    out=resolve_mentions(c,body,ch,author); c.close(); return out

def message_to_json(r):
    c=connect(); author=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    reps=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    cnt=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]; c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(author),'body':r['body'],'parent_id':r['parent_id'],'reply_count':cnt,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in reps],'mentions':mention_ids(r['body'], r['channel_id'], r['author_id'])}
def create_message(user,cid,body,parent=None):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        r=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,user['id'],body,parent,now_iso())).fetchone()
        row=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone(); msg=message_to_json(row)
        emit_event(c,cid,'message.reply' if parent else 'message.created',{'message':msg}); return msg
def list_messages(cid):
    c=connect(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(cid,)).fetchall(); c.close(); return [message_to_json(x) for x in rows]
def list_replies(mid):
    c=connect(); rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,)).fetchall(); c.close(); return [message_to_json(x) for x in rows]
def react(user,mid,emoji,add=True):
    with tx() as c:
        if add:c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,user['id'],emoji))
        else:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji))
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); return message_to_json(r)['reactions']
def edit_message(user,mid,body):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r: raise LookupError()
        if r['deleted']: raise RuntimeError()
        if r['author_id']!=user['id']: raise PermissionError()
        t=now_iso(); c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,t,mid)); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); m=message_to_json(r); m['mentions']=resolve_mentions(c,m['body'],c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone(),r['author_id']); emit_event(c,r['channel_id'],'message.updated',{'message':m}); return m
def delete_message(user,mid):
    with tx() as c:
        r=c.execute('SELECT m.*,w.owner_id FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN workspaces w ON w.id=ch.workspace_id WHERE m.id=?',(mid,)).fetchone()
        if not r: raise LookupError()
        if r['author_id']!=user['id'] and r['owner_id']!=user['id']: raise PermissionError()
        if not r['deleted']: c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid})
        return True
if __name__=='__main__': init_db()
