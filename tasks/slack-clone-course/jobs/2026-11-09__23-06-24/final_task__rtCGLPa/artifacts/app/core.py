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
    c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);'''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+':'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try: s,h=stored.split(':',1); return hashlib.sha256((s+pw).encode()).hexdigest()==h
    except: return False
def issue_token(c,user_id):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,user_id)); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return dict(r)
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def channel_to_json(r): return dict(r)
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(ch):
    r=get_channel(ch); return r['last_event_id'] if r else 0
def events_after(ch,since):
    c=connect(); r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall(); c.close(); return [json.loads(x[0]) for x in r]
def globals_after(since):
    c=connect(); r=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall(); c.close(); return [(x['id'],json.loads(x['payload'])) for x in r]
if __name__=='__main__':
    init_db()

def message_to_json(r):
    c=connect()
    author=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    replies=c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    rr=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    result={'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(author),
            'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],
            'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(v) for v in x['ids'].split(',')]} for x in rr],
            'mentions':[]}
    ch=c.execute('SELECT workspace_id FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    if ch and ch['workspace_id']:
        names=re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',r['body'] or '')
        if names:
            qs=','.join('?'*len(names))
            rows=c.execute(f'SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.workspace_id=? AND u.username IN ({qs})',(ch['workspace_id'],*names)).fetchall()
            result['mentions']=list(dict.fromkeys(x['id'] for x in rows if x['id']!=r['author_id']))
    c.close()
    return result
def edit_message(user,message_id,body):
    if not isinstance(body,str) or not body.strip(): raise ValueError('invalid body')
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not r: raise LookupError('not found')
        if r['deleted']: raise RuntimeError('deleted')
        if r['author_id']!=user['id']: raise PermissionError('forbidden')
        edited=now_iso(); c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,edited,message_id))
        n=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        frame=emit_event(c,r['channel_id'],'message.updated',{'message':{'id':n['id'],'channel_id':n['channel_id'],'author_id':n['author_id'],'body':n['body'],'parent_id':n['parent_id'],'created_at':n['created_at'],'edited_at':n['edited_at'],'reply_count':0,'files':[],'reactions':[],'mentions':[]}})
    return message_to_json(n)
def delete_message(user,message_id):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not r: raise LookupError('not found')
        if r['deleted']: return False
        ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
        role=member_role(ch['workspace_id'],user['id']) if ch and ch['workspace_id'] else None
        if r['author_id']!=user['id'] and role!='owner': raise PermissionError('forbidden')
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(message_id,))
        emit_event(c,r['channel_id'],'message.deleted',{'message_id':message_id})
    return True
