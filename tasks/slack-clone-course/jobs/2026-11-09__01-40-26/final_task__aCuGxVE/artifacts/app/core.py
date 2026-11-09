import sqlite3, os, hashlib, secrets, base64, json, re, datetime, sys
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
class tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    os.makedirs('/app/data',exist_ok=True)
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,creator_id INTEGER,invited_user_id INTEGER,expires_at REAL,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,emoji TEXT,user_id INTEGER,ord INTEGER,PRIMARY KEY(message_id,emoji,user_id));
    CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    ''')
    cols=[x[1] for x in c.execute('PRAGMA table_info(messages)').fetchall()]
    if 'mentions' not in cols: c.execute("ALTER TABLE messages ADD COLUMN mentions TEXT DEFAULT '[]'")
    c.execute('CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id))')
    c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+':'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split(':',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?,?)',(t,uid,now_iso())); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r):
    return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def get_workspace_by_id(wid):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); c.close(); return r
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g:add_channel_member(c,g['id'],uid)
def add_channel_member(c,cid,uid):
    before=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone()
    if before:return False
    c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,uid))
    try: emit_event(c,cid,'member.joined',{'user_id':uid})
    except sqlite3.OperationalError: pass
    return True
def remove_channel_member(c,cid,uid):
    before=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone()
    if not before:return False
    c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid))
    emit_event(c,cid,'member.left',{'user_id':uid})
    return True
def create_channel_row(c,wid,name,private=False,topic=''):
    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic)); return x.lastrowid
def emit_event(c,cid,kind,payload):
    c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(cid,))
    n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':cid,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return frame
if __name__=='__main__':
    if '--init-db' in sys.argv:init_db()
def can_read_channel(uid,ch):
    if ch['is_private']:
        c=connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close(); return bool(x)
    return bool(member_role(ch['workspace_id'],uid) in ('owner','admin','member'))
def require_writable(ch):
    if ch['is_archived']: raise ValueError('archived')
def message_to_json(r):
    c=connect(); a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    n=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]; c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[file_to_json(x) for x in connect().execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()],'reactions':reactions_for(r['id']),'mentions':json.loads(r['mentions'] or '[]')}
def create_message(user,cid,body,parent_id=None):
    if not isinstance(body,str) or not body.strip(): raise ValueError('body')
    ch=get_channel(cid); require_writable(ch)
    with tx() as c:
        if parent_id is not None:
            p=c.execute('SELECT * FROM messages WHERE id=? AND channel_id=? AND parent_id IS NULL AND deleted=0',(parent_id,cid)).fetchone()
            if not p: raise ValueError('parent')
        mentions=resolve_mentions(c,body,ch,user['id'])
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,user['id'],body,parent_id,now_iso(),json.dumps(mentions)))
        mid=cur.lastrowid
        kind='message.reply' if parent_id is not None else 'message.created'
        rr=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        emit_event(c,cid,kind,{'message':{'id':mid,'channel_id':cid,'author_id':user['id'],'body':body,'parent_id':parent_id,'created_at':rr['created_at']}})
    c=connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close(); return r
def list_messages(cid,limit=50,cursor=None):
    c=connect(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC LIMIT ?', (cid,limit+1)).fetchall(); c.close()
    more=len(rows)>limit; rows=rows[:limit]
    return rows, (encode_cursor('messages',rows[-1]['id']) if more and rows else None)
def encode_cursor(kind,v):
    return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':int(v)}).encode()).decode().rstrip('=')
def head_event_id(cid):
    c=connect()
    r=c.execute('SELECT COALESCE(last_event_id,0) FROM channels WHERE id=?',(cid,)).fetchone()
    c.close()
    return int(r[0]) if r else 0
def events_after(cid,since):
    c=connect()
    rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,int(since))).fetchall()
    c.close()
    return [{'event_id':r['event_id'],'payload':r['payload']} for r in rows]

def group_to_json(r):
    c=connect(); ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY rowid',(r['id'],)).fetchall()]; c.close()
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
def create_group(wid,creator,handle,name,user_ids):
    if not re.fullmatch(r'[a-z0-9-]{1,32}',handle) or not isinstance(name,str) or not isinstance(user_ids,list): raise ValueError('invalid group')
    with tx() as c:
        if c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone(): raise FileExistsError()
        allowed={x[0] for x in c.execute('SELECT user_id FROM members WHERE workspace_id=?',(wid,)).fetchall()}
        if any(isinstance(x,bool) or not isinstance(x,int) or x not in allowed for x in user_ids): raise ValueError('invalid members')
        cur=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(wid,handle,name,creator)); gid=cur.lastrowid
        for uid in dict.fromkeys(user_ids): c.execute('INSERT INTO group_members VALUES(?,?)',(gid,uid))
    c=connect();r=c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone();c.close();return r
def update_group(gid,name,user_ids):
    with tx() as c:
        if name is not None:c.execute('UPDATE groups SET name=? WHERE id=?',(name,gid))
        if user_ids is not None:
            c.execute('DELETE FROM group_members WHERE group_id=?',(gid,))
            for uid in dict.fromkeys(user_ids):c.execute('INSERT INTO group_members VALUES(?,?)',(gid,uid))
    c=connect();r=c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone();c.close();return r
def delete_group(gid):
    with tx() as c:c.execute('DELETE FROM group_members WHERE group_id=?',(gid,));c.execute('DELETE FROM groups WHERE id=?',(gid,))
    return True
def resolve_mentions(c,body,channel,author_id):
    out=[]
    if '@channel' in body or '@here' in body:
        ids=[x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],)).fetchall()]
        if '@channel' in body: out += [x for x in ids if x!=author_id]
        else: out += [x for x in ids if x!=author_id and x in present_user_ids()]
    for name in re.findall(r'(?<![\w@.])@([\w-]+)',body):
        if name in ('channel','here'): continue
        g=c.execute('SELECT id FROM groups WHERE workspace_id=? AND handle=?',(channel['workspace_id'],name)).fetchone()
        if g:
            ids=[x[0] for x in c.execute('SELECT gm.user_id FROM group_members gm JOIN channel_members cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=?',(g[0],channel['id'])).fetchall()]
            for uid in ids:
                if uid!=author_id and uid not in out:out.append(uid)
            continue
        x=c.execute('SELECT u.id FROM users u JOIN members m ON m.user_id=u.id WHERE m.workspace_id=? AND u.username=?',(channel['workspace_id'],name)).fetchone()
        if x and x[0]!=author_id and x[0] not in out:out.append(x[0])
    return list(dict.fromkeys(out))

def read_state(uid,cid):
    c=connect()
    x=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone()
    last=x[0] if x else 0
    msgs={r['id']:r for r in c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0',(cid,)).fetchall()}
    evs=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? ORDER BY event_id',(cid,)).fetchall()
    unread=[]
    for e in evs:
        try:
            f=json.loads(e['payload']); m=f.get('message',{}); mid=m.get('id')
            if e['event_id']>last and mid in msgs and msgs[mid]['author_id']!=uid: unread.append(msgs[mid])
        except: pass
    mention=sum(uid in json.loads(r['mentions'] or '[]') for r in unread)
    c.close()
    return {'channel_id':cid,'last_read_event_id':last,'unread_count':len(unread),'mention_count':mention}

def mark_read(user,cid,body):
    n=body.get('last_read_event_id') if isinstance(body,dict) else None
    if isinstance(n,bool) or not isinstance(n,int) or n<0: raise ValueError('invalid read state')
    with tx() as c:
        c.execute('INSERT INTO read_states(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=MAX(last_read_event_id,excluded.last_read_event_id)',(user['id'],cid,n))
    return read_state(user['id'],cid)

def reactions_for(mid):
    c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY ord',(mid,)).fetchall(); c.close()
    out=[]
    for r in rows:
        x=next((z for z in out if z['emoji']==r['emoji']),None)
        if not x:x={'emoji':r['emoji'],'count':0,'user_ids':[]};out.append(x)
        x['count']+=1;x['user_ids'].append(r['user_id'])
    return out
def react(user,mid,emoji,add):
    if not isinstance(emoji,str) or not emoji:return None,'bad'
    c=connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return None,'missing'
    ch=get_channel(m['channel_id'])
    if not can_read_channel(user['id'],ch):return None,'forbidden'
    if ch['is_archived']:return None,'archived'
    changed=False
    with tx() as c:
        exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,user['id'])).fetchone()
        if add and not exists:
            n=c.execute('SELECT COALESCE(MAX(ord),0)+1 FROM reactions WHERE message_id=?',(mid,)).fetchone()[0]
            c.execute('INSERT INTO reactions VALUES(?,?,?,?)',(mid,emoji,user['id'],n));emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':user['id']});changed=True
        elif not add and exists:
            c.execute('DELETE FROM reactions WHERE message_id=? AND emoji=? AND user_id=?',(mid,emoji,user['id']));emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':user['id']});changed=True
    return reactions_for(mid),None

def open_dm(uid,other):
    a,b=sorted((uid,other))
    with tx() as c:
        x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if x:return x[0]
        c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',('',1)); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid)); c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,uid,cid,other))
    return cid
def file_to_json(r):
    return {'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}

def touch_presence(conn_id,user_id):
    import time
    with tx() as c:c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
    with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
def present_user_ids():
    import time
    c=connect(); r=[x[0] for x in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall()];c.close();return r

def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def globals_after(since):
    c=connect(); rows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall(); c.close()
    return [{'id':r['id'],'payload':r['payload']} for r in rows]
