import sqlite3, os, json, hashlib, secrets, base64, re, argparse, datetime, time
from contextlib import contextmanager
from zoneinfo import ZoneInfo
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
@contextmanager
def tx():
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
    except: c.execute('ROLLBACK'); raise
    finally: c.close()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    os.makedirs('/app/data/files',exist_ok=True); c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    '''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+':'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split(':',1); return hashlib.sha256((s+pw).encode()).hexdigest()==h
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
    if not t:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def channel_to_json(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def create_channel_row(c,w,name,private=False,topic='',dm=False):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm,topic) VALUES(?,?,?,?,?)',(w,name,int(private),int(dm),topic)); return c.execute('SELECT * FROM channels WHERE id=?',(cur.lastrowid,)).fetchone()
def add_channel_member(c,ch,u):
    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:add_channel_member(c,g['id'],u)
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':ch}; frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(ch):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r['last_event_id'] if r else 0
def events_after(ch,s):
    c=connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,s)).fetchall(); c.close(); return [json.loads(r['payload']) for r in rows]
def globals_after(s):
    c=connect(); rows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(s,)).fetchall(); c.close(); return [(r['id'],json.loads(r['payload'])) for r in rows]
def can_read_channel(u,ch):
    c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone(); c.close(); return bool(r)
def require_writable(ch): return bool(ch and ch['is_archived'])
def resolve_mentions(c,body,ch,author):
    out=set()
    if ch['workspace_id']:
        toks=set(re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body))
        for x in toks:
            if x=='channel':
                out.update(r['user_id'] for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],author)))
            elif x=='here':
                active={z['user_id'] for z in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))}
                out.update(r['user_id'] for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],author)) if r['user_id'] in active)
            else:
                r=c.execute('SELECT id FROM users WHERE username=?',(x,)).fetchone()
                if r and r['id']!=author and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],r['id'])).fetchone():out.add(r['id'])
                gs=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(ch['workspace_id'],x)).fetchone()
                if gs:
                    out.update(z['user_id'] for z in c.execute('SELECT gm.user_id FROM group_members gm JOIN channel_members cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=? AND gm.user_id!=?',(gs['id'],ch['id'],author)))

    return sorted(out)
def reactions_for(mid,c=None):
    own=c is None
    if c is None:c=connect()
    rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall()
    if own:c.close()
    d={}
    for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
    return [{'emoji':e,'count':len(v),'user_ids':v} for e,v in d.items()]
def message_to_json(r):
    c=connect(); a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    n=c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']; fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()
    c.close(); return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[file_to_json(x) for x in fs],'reactions':reactions_for(r['id']),'mentions':json.loads(r['mentions']) if 'mentions' in r.keys() and r['mentions'] else []}
def create_message(user,ch,body,parent=None,file_ids=None):
    with tx() as c:
        channel=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
        if not channel or require_writable(channel): raise ValueError('archived')
        if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,user['id'])).fetchone(): raise PermissionError()
        if not isinstance(body,str) or not body.strip(): raise ValueError('body')
        m=now_iso(); mentions=resolve_mentions(c,body,channel,user['id'])
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch,user['id'],body,parent,m,json.dumps(mentions)))
        mid=cur.lastrowid
        for fid in file_ids or []:
            if not c.execute('SELECT 1 FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,user['id'])).fetchone(): raise ValueError('file')
            c.execute('UPDATE files SET message_id=? WHERE id=?',(mid,fid))
        row=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        obj={'id':row['id'],'channel_id':row['channel_id'],'author_id':row['author_id'],'author':user_to_json(c.execute('SELECT * FROM users WHERE id=?',(row['author_id'],)).fetchone()),'body':row['body'],'parent_id':row['parent_id'],'reply_count':c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(mid,)).fetchone()['n'],'created_at':row['created_at'],'edited_at':row['edited_at'],'files':[file_to_json(x) for x in c.execute('SELECT * FROM files WHERE message_id=?',(mid,)).fetchall()],'reactions':reactions_for(mid,c),'mentions':json.loads(row['mentions'] or '[]')}
        emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':obj})
        return obj
def list_messages(ch, replies=False,parent=None):
    c=connect(); q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND '+('parent_id=?' if replies else 'parent_id IS NULL')+' ORDER BY id DESC'
    rows=c.execute(q,(ch,parent) if replies else (ch,)).fetchall(); c.close(); return [message_to_json(r) for r in rows]
def edit_message(user,mid,body):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r: raise LookupError()
        if r['deleted']: raise RuntimeError()
        if r['author_id']!=user['id']: raise PermissionError()
        if not isinstance(body,str) or not body.strip(): raise ValueError()
        ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone(); mentions=resolve_mentions(c,body,ch,user['id'])
        c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(body,now_iso(),json.dumps(mentions),mid))
        obj=message_to_json(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); emit_event(c,r['channel_id'],'message.updated',{'message':obj}); return obj
def delete_message(user,mid):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r: raise LookupError()
        if r['deleted']: return False
        ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
        if r['author_id']!=user['id'] and member_role(ch['workspace_id'],user['id'])!='owner': raise PermissionError()
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid}); return True
def react(user,mid,emoji,add=True):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not r: raise LookupError()
        if add:
            cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,user['id'],emoji)); changed=cur.rowcount>0; kind='reaction.added'
        else:
            cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji)); changed=cur.rowcount>0; kind='reaction.removed'
        if changed: emit_event(c,r['channel_id'],kind,{'message_id':mid,'emoji':emoji,'user_id':user['id']})
        return reactions_for(mid,c)
def file_to_json(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def save_file(user,filename,ctype,data):
    with tx() as c:
        cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(user['id'],filename,ctype,len(data),now_iso())); fid=cur.lastrowid
        open('/app/data/files/'+str(fid),'wb').write(data); return file_to_json(c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone())
def open_dm(user,other):
    a,b=sorted((user['id'],other))
    with tx() as c:
        r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if r:return r['channel_id']
        ch=create_channel_row(c,None,'dm-%s-%s'%(a,b),True,'',True); c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,ch['id'])); add_channel_member(c,ch['id'],a); add_channel_member(c,ch['id'],b); return ch['id']
if __name__=='__main__':
    init_db()
def touch_presence(conn_id,user_id):
    with tx() as c:c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
    with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
def present_user_ids():
    c=connect();r=c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall();c.close();return {x['user_id'] for x in r}
