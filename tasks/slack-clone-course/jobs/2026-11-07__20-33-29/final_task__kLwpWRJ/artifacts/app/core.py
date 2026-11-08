import sqlite3, os, json, hashlib, secrets, base64, re, datetime, argparse, time
from contextlib import contextmanager
from zoneinfo import ZoneInfo
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
@contextmanager
def tx():
    c=connect()
    try: c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
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
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,reply_count INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    '''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try: s,h=stored.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(wid,uid,role))
    g=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(wid,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],uid))
def create_channel_row(c,wid,name,private=False,topic=''):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic)); return c.execute('SELECT * FROM channels WHERE id=?',(cur.lastrowid,)).fetchone()
def channel_to_json(r):
    d={k:r[k] for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
    d['is_private']=bool(d['is_private']); d['is_dm']=bool(d['is_dm']); d['is_archived']=bool(d['is_archived']); return d
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def add_channel_member(c,cid,uid): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid))
def remove_channel_member(c,cid,uid): c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid))
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':cid,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return frame
def emit_global(c,payload): c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def head_event_id(cid):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r['last_event_id'] if r else 0
def events_after(cid,since):
    c=connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); c.close(); return [json.loads(r['payload']) for r in rows]
def globals_after(since):
    c=connect(); rows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall(); c.close(); return [(r['id'],json.loads(r['payload'])) for r in rows]
def encode_cursor(kind,v): return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':int(v)}).encode()).decode().rstrip('=')
def decode_cursor(kind,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4))); assert x['k']==kind and isinstance(x['v'],int); return x['v']
    except: raise ValueError()
def parse_paging(q,kind):
    try: limit=int(q.get('limit','50')); assert 1<=limit<=200
    except: raise ValueError()
    cur=q.get('cursor'); return limit, (decode_cursor(kind,cur) if cur else None)
def can_read_channel(uid,ch):
    c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close(); return bool(r)
def require_writable(ch):
    if ch and ch['is_archived']: raise PermissionError('archived')
def message_to_json(r):
    c=connect(); author=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall(); c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(author),'body':r['body'],'parent_id':r['parent_id'],'reply_count':r['reply_count'],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[file_to_json(x) for x in fs],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs],'mentions':[]}
def resolve_mentions(c,body,ch,author_id):
    ids=set(); ws=ch['workspace_id']
    if not ws:return []
    for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_-]+)',body):
        u=c.execute('SELECT u.id FROM users u JOIN workspace_members m ON m.user_id=u.id WHERE m.workspace_id=? AND u.username=?',(ws,name)).fetchone()
        if u:ids.add(u['id'])
        g=c.execute('SELECT g.id FROM groups_ g WHERE g.workspace_id=? AND g.handle=?',(ws,name)).fetchone()
        if g:
            ids.update(x['user_id'] for x in c.execute('SELECT gm.user_id FROM group_members gm JOIN channel_members cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=?',(g['id'],ch['id'])))
    if re.search(r'(?<!\w)@channel\b',body):
        ids.update(x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)))
    if re.search(r'(?<!\w)@here\b',body):
        ids.update(x for x in present_user_ids() if c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],x)).fetchone())
    return sorted(ids-{author_id})

def create_message(user,cid,body,parent_id=None,file_ids=None):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not ch: raise LookupError()
        if not can_read_channel(user['id'],ch): raise PermissionError()
        require_writable(ch)
        if not isinstance(body,str) or not body.strip(): raise ValueError()
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,reply_count) VALUES(?,?,?,?,?,0)',(cid,user['id'],body,parent_id,now_iso()))
        mid=cur.lastrowid
        if parent_id:c.execute('UPDATE messages SET reply_count=reply_count+1 WHERE id=?',(parent_id,))
        for fid in file_ids or []:
            f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not f or f['uploader_id']!=user['id'] or f['message_id'] is not None: raise ValueError()
            c.execute('UPDATE files SET message_id=? WHERE id=?',(mid,fid))
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        msg={'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()),'body':r['body'],'parent_id':r['parent_id'],'reply_count':r['reply_count'],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[file_to_json(x) for x in c.execute('SELECT * FROM files WHERE message_id=?',(mid,))],'reactions':[],'mentions':resolve_mentions(c,body,ch,user['id'])}
        msg['reactions']=reactions_for(mid); emit_event(c,cid,'message.reply' if parent_id else 'message.created',{'message':msg})
        return msg
def list_messages(cid,limit,cursor):
    c=connect(); q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'; args=[cid]
    if cursor is not None:q+=' AND id<?';args.append(cursor)
    q+=' ORDER BY id DESC LIMIT ?';args.append(limit+1); rows=c.execute(q,args).fetchall(); c.close()
    return rows[:limit],(encode_cursor('messages',rows[limit-1]['id']) if len(rows)>limit else None)
def list_replies(pid,limit,cursor):
    c=connect(); q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';args=[pid]
    if cursor is not None:q+=' AND id>?';args.append(cursor)
    q+=' ORDER BY id LIMIT ?';args.append(limit+1); rows=c.execute(q,args).fetchall();c.close()
    return rows[:limit],(encode_cursor('replies',rows[limit-1]['id']) if len(rows)>limit else None)
def reactions_for(mid):
    c=connect(); rows=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall();c.close()
    return [{'emoji':r['emoji'],'count':r['n'],'user_ids':[int(x) for x in r['ids'].split(',')]} for r in rows]
def react(user,mid,emoji,add=True):
    with tx() as c:
        m=c.execute('SELECT m.*,ch.* FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=? AND m.deleted=0',(mid,)).fetchone()
        if not m: raise LookupError()
        if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],user['id'])).fetchone(): raise PermissionError()
        before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji)).fetchone()
        if add:
            if before:return reactions_for(mid)
            c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,user['id'],emoji));kind='reaction.added'
        else:
            if not before:return reactions_for(mid)
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji));kind='reaction.removed'
        rows=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(mid,)).fetchall()
        result=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rows]
        emit_event(c,m['channel_id'],kind,{'message_id':mid,'emoji':emoji,'user_id':user['id'],'reactions':result})
        return result

def edit_message(user,mid,body):
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m:raise LookupError()
        if m['author_id']!=user['id']:raise PermissionError()
        if m['deleted']:raise RuntimeError()
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,now_iso(),mid));r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();msg=message_to_json(r);emit_event(c,m['channel_id'],'message.updated',{'message':msg});return msg
def delete_message(user,mid):
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m:return True
        role=member_role(c.execute('SELECT workspace_id FROM channels WHERE id=?',(m['channel_id'],)).fetchone()['workspace_id'],user['id'])
        if m['author_id']!=user['id'] and role not in ('owner','admin'):raise PermissionError()
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid});return True
def save_file(user,filename,content_type,data):
    with tx() as c:
        cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(user['id'],filename,content_type,len(data),now_iso()));fid=cur.lastrowid
        open(f'/app/data/files/{fid}','wb').write(data);return c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
def file_to_json(r):return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def open_dm(user,other_id):
    with tx() as c:
        if not c.execute('SELECT id FROM users WHERE id=?',(other_id,)).fetchone():raise LookupError()
        a,b=sorted((user['id'],other_id));r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if r:return r['channel_id']
        cur=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)');cid=cur.lastrowid
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));c.executemany('INSERT INTO channel_members VALUES(?,?)',[(cid,a),(cid,b)]);return cid
def create_group(user,wid,handle,name,user_ids):
    if not re.fullmatch(r'[a-z0-9-]{1,32}',handle):raise ValueError()
    with tx() as c:
        if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone():raise FileExistsError()
        members={x['user_id'] for x in c.execute('SELECT user_id FROM workspace_members WHERE workspace_id=?',(wid,))}
        if not set(user_ids)<=members:raise ValueError()
        cur=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(wid,handle,name,user['id']));gid=cur.lastrowid
        c.execute('DELETE FROM group_members WHERE group_id=?',(gid,));c.executemany('INSERT INTO group_members VALUES(?,?)',[(gid,x) for x in user_ids]);return c.execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone()
def group_to_json(r):
    c=connect();ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(r['id'],))];c.close()
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
def update_group(user,wid,handle,name=None,user_ids=None):
    with tx() as c:
        r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone()
        if not r:raise LookupError()
        if r['creator_id']!=user['id'] and c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=? AND role="owner"',(wid,user['id'])).fetchone() is None:raise PermissionError()
        if name is not None:c.execute('UPDATE groups_ SET name=? WHERE id=?',(name,r['id']))
        if user_ids is not None:
            members={x['user_id'] for x in c.execute('SELECT user_id FROM workspace_members WHERE workspace_id=?',(wid,))}
            if not set(user_ids)<=members:raise ValueError()
            c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],));c.executemany('INSERT INTO group_members VALUES(?,?)',[(r['id'],x) for x in user_ids])
        return c.execute('SELECT * FROM groups_ WHERE id=?',(r['id'],)).fetchone()
def delete_group(user,wid,handle):
    with tx() as c:
        r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(wid,handle)).fetchone()
        if r and r['creator_id']!=user['id'] and not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=? AND role="owner"',(wid,user['id'])).fetchone():raise PermissionError()
        if r:c.execute('DELETE FROM groups_ WHERE id=?',(r['id'],))
        return True
def touch_presence(conn_id,user_id):
    with tx() as c:c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,user_id,time.time()))
def drop_presence(conn_id):
    with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
def present_user_ids():
    c=connect();r=[x['user_id'] for x in c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,))];c.close();return set(r)
def update_profile(user,fields):
    allowed={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512),'timezone':None}
    with tx() as c:
        for k,v in fields.items():
            if k not in allowed or not isinstance(v,str):raise ValueError()
            if allowed[k] and not allowed[k][0]<=len(v)<=allowed[k][1]:raise ValueError()
            if k=='timezone':
                try:ZoneInfo(v)
                except:raise ValueError()
        if fields:
            sets=','.join(f'{k}=?' for k in fields);c.execute(f'UPDATE users SET {sets} WHERE id=?',(*fields.values(),user['id']))
            r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone();emit_global(c,{'type':'user.updated','user':user_to_json(r)});return r
        return c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
def run_slash(user,ch,body):
    if body.startswith('//'):return {'body':body[1:]}
    if not body.startswith('/'):return {'body':body}
    cmd,*rest=body[1:].split(' ',1);text=rest[0] if rest else ''
    if cmd=='me':
        if not text.strip():raise ValueError()
        return {'body':body}
    if cmd=='shrug':return {'body':(text+' ' if text else '')+'¯\\_(ツ)_/¯'}
    if cmd=='topic':
        if not can_read_channel(user['id'],ch):raise PermissionError()
        with tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(text,ch['id']));r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        return {'side':'channel','channel':r}
    if cmd=='invite':
        if not text.startswith('@') or not text[1:]:raise ValueError()
        with tx() as c:
            u=c.execute('SELECT * FROM users WHERE username=?',(text[1:],)).fetchone()
            if not u:raise ValueError()
            if not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone():raise ValueError()
            add_channel_member(c,ch['id'],u['id'])
        return {'side':'invite','invited_user':u}
    if cmd in ('archive','unarchive'):
        if member_role(ch['workspace_id'],user['id'])!='owner':raise PermissionError()
        with tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if cmd=='archive' else 0,ch['id']));r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        return {'side':'channel','channel':r}
    raise ValueError()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--init-db',action='store_true')
    if p.parse_args().init_db:init_db()
