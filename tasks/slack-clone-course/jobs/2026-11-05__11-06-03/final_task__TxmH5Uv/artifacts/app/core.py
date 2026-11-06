import sqlite3, os, json, secrets, hashlib, base64, re
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
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,created_by INTEGER,expires_at REAL,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,invited_user_id INTEGER);
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    '''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try: s,h=stored.split('$',1); return hashlib.sha256((s+pw).encode()).hexdigest()==h
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES (?,?)',(t,uid)); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def update_profile(user,fields):
    allowed={'display_name':(str,80),'timezone':(str,128),'status_text':(str,140),'status_emoji':(str,32),'avatar_url':(str,512)}
    vals=[];args=[]
    from zoneinfo import ZoneInfo
    for k,v in fields.items():
        if k not in allowed:return None
        typ,maxlen=allowed[k]
        if not isinstance(v,typ) or not v or len(v)>maxlen:return None
        if k=='timezone':
            try: ZoneInfo(v)
            except: return None
        vals.append(k+'=?');args.append(v)
    if not vals:return None
    with tx() as c:
        args.append(user['id']);c.execute('UPDATE users SET '+','.join(vals)+' WHERE id=?',args)
        row=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        emit_global(c,{'type':'user.updated','user':user_to_json(row)})
    return row
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
    c=connect();r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone();c.close();return r['role'] if r else None
def can_read_channel(uid,ch):
    if ch['is_dm']:
        c=connect();r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone();c.close();return bool(r)
    return bool(member_role(ch['workspace_id'],uid)) and (not ch['is_private'] or _cm(ch['id'],uid))
def _cm(cid,uid):
    c=connect();r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone();c.close();return bool(r)
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':cid,**payload}; c.execute('INSERT INTO events VALUES (?,?,?)',(cid,n,json.dumps(frame))); return frame
def head_event_id(cid):
    c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone();c.close();return r[0] if r else 0
def events_after(cid,since):
    c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall();c.close();return [json.loads(x[0]) for x in r]
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4))); assert x['k']==k and type(x['v']) is int; return x['v']
    except: raise ValueError()
def parse_paging(q,k):
    try:
        n=int(q.get('limit','50')); assert 1<=n<=200
        v=decode_cursor(k,q['cursor']) if q.get('cursor') else None; return n,v
    except: raise ValueError()
def add_channel_member(c,cid,uid): c.execute('INSERT OR IGNORE INTO channel_members VALUES (?,?)',(cid,uid))
def create_channel_row(c,wid,name,private=False,topic=''):
    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic)); return c.execute('SELECT * FROM channels WHERE id=?',(x.lastrowid,)).fetchone()
def resolve_mentions(c, body, channel, author_id):
    out=[]
    def add(uid):
        if uid and uid!=author_id and uid not in out: out.append(uid)
    members={x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],)).fetchall()}
    if re.search(r'(?<![\w@.])@channel(?!\w)',body):
        for uid in members:add(uid)
    names=re.findall(r'(?<![\w@.])@(\w+)',body)
    for name in names:
        if name in ('channel','here'): continue
        x=c.execute('SELECT u.id FROM users u JOIN workspace_members wm ON wm.user_id=u.id WHERE u.username=? AND wm.workspace_id=?',(name,channel['workspace_id'])).fetchone()
        if x and x['id'] in members:add(x['id'])
        g=c.execute('SELECT user_ids FROM groups_ WHERE workspace_id=? AND handle=?',(channel['workspace_id'],name)).fetchone()
        if g:
            for uid in json.loads(g['user_ids'] or '[]'):
                if uid in members:add(uid)
    if re.search(r'(?<![\w@.])@here(?!\w)',body):
        active={x['user_id'] for x in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',( __import__('time').time()-3,)).fetchall()}
        for uid in members:
            if uid in active:add(uid)
    return out

def message_to_json(r):
    c=connect(); author=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); rr=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(r['id'],)).fetchall()
    reply_count=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0] if r['parent_id'] is None else 0
    fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall(); c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(author),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reply_count,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[file_to_json(x) for x in fs],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rr],'mentions':json.loads(r['mentions'] or '[]')}
def group_to_json(r):
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'] or '[]')}
def create_group(user,wid,handle,name,user_ids):
    with tx() as c:
        x=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(wid,handle,name,user['id'],json.dumps(user_ids)))
        return group_to_json(c.execute('SELECT * FROM groups_ WHERE id=?',(x.lastrowid,)).fetchone())
def update_group(wid,handle,name,user_ids):
    with tx() as c:
        c.execute('UPDATE groups_ SET name=?,user_ids=? WHERE workspace_id=? AND handle=?',(name,json.dumps(user_ids),wid,handle))
        r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone()
        return group_to_json(r) if r else None
def file_to_json(r):
    return {'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}
def save_file(user,filename,content_type,data):
    os.makedirs('/app/data/files',exist_ok=True)
    with tx() as c:
        x=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(user['id'],filename,content_type,len(data),now_iso()))
        fid=x.lastrowid
        with open('/app/data/files/'+str(fid),'wb') as f:f.write(data)
        return file_to_json(c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone())
def open_dm(user,other_id):
    if user['id']==other_id:return None
    a,b=sorted((user['id'],other_id))
    with tx() as c:
        x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if x:return x['channel_id']
        q=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)')
        cid=q.lastrowid
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid))
        c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,b))
        return cid

def edit_message(user,message_id,body):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not r:return ('404',None)
        if r['deleted']:return ('409',None)
        if r['author_id']!=user['id']:return ('403',None)
        ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
        mentions=resolve_mentions(c,body,ch,user['id'])
        edited=now_iso()
        c.execute('UPDATE messages SET body=?,mentions=?,edited_at=? WHERE id=?',(body,json.dumps(mentions),edited,message_id))
        row=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        msg=message_to_json(row); emit_event(c,r['channel_id'],'message.updated',{'message':msg})
        return ('ok',msg)
def delete_message(user,message_id):
    with tx() as c:
        r=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not r:return ('404',False)
        if r['deleted']:return ('ok',True)
        ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
        owner=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch['workspace_id'] else None
        if r['author_id']!=user['id'] and (not owner or owner['owner_id']!=user['id']):return ('403',False)
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(message_id,))
        emit_event(c,r['channel_id'],'message.deleted',{'message_id':message_id})
        return ('ok',True)
def create_message(user,cid,body,parent_id=None):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        mentions=resolve_mentions(c,body,ch,user['id'])
        x=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,user['id'],body,parent_id,now_iso(),json.dumps(mentions)))
        r=c.execute('SELECT * FROM messages WHERE id=?',(x.lastrowid,)).fetchone(); m=message_to_json(r); emit_event(c,cid,'message.reply' if parent_id else 'message.created',{'message':m}); return m
if __name__=='__main__':
    import sys
    if '--init-db' in sys.argv:init_db()
