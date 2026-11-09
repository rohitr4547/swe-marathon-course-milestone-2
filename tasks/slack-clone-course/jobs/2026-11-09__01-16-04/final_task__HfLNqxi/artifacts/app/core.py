import sqlite3, os, json, hashlib, secrets, base64, re, datetime, argparse
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
    os.makedirs('/app/data/files',exist_ok=True)
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at REAL,max_uses INTEGER,uses INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS groups_tbl(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
    '''); c.close()
def hash_password(pw):
    salt=secrets.token_hex(16); return salt+'$'+hashlib.sha256((salt+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try: salt,d=stored.split('$',1); return secrets.compare_digest(d,hashlib.sha256((salt+pw).encode()).hexdigest())
    except: return False
def issue_token(c,user_id):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?,?)',(t,user_id,now_iso())); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r):
    return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g[0],u))
def create_channel_row(c,w,name,private=False,topic=''):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic))
    i=cur.lastrowid
    owner=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(w,)).fetchone()[0]
    c.execute('INSERT INTO channel_members VALUES(?,?)',(i,owner))
    return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def create_message(user,ch,body,parent_id=None):
    with tx() as c:
        r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
        x=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,user['id'],body,parent_id,now_iso()))
        m=c.execute('SELECT * FROM messages WHERE id=?',(x.lastrowid,)).fetchone()
        emit_event(c,ch,'message.reply' if parent_id else 'message.created',{'message':message_to_json(m)})
        return message_to_json(m)
def message_to_json(r):
    c=connect()
    u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    n=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    mentions=resolve_mentions(c,r['body'],ch,r['author_id'])
    files=[file_to_json(x) for x in c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()]
    c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':files,'reactions':reactions_for(r['id']),'mentions':mentions}

def head_event_id(ch):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r[0] if r else 0
def events_after(ch,since):
    c=connect(); r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since,)).fetchall(); c.close(); return [json.loads(x[0]) for x in r]
if __name__=='__main__':
    if '--init-db' in __import__('sys').argv:init_db()

def add_channel_member(c,channel_id,user_id):
    before=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel_id,user_id)).fetchone()
    if not before:
        c.execute('INSERT INTO channel_members VALUES(?,?)',(channel_id,user_id))
        emit_event(c,channel_id,'member.joined',{'user_id':user_id})
    return not bool(before)
def remove_channel_member(c,channel_id,user_id):
    cur=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(channel_id,user_id))
    if cur.rowcount:
        emit_event(c,channel_id,'member.left',{'user_id':user_id}); return True
    return False
def can_read_channel(user_id,channel):
    c=connect()
    if c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel['id'],user_id)).fetchone(): c.close(); return True
    if channel['is_private'] or not channel['workspace_id']: c.close(); return False
    r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(channel['workspace_id'],user_id)).fetchone(); c.close()
    return bool(r and r['role']!='guest')
def require_writable(channel):
    return not bool(channel['is_archived'])
def validate_topic(topic):
    return isinstance(topic,str) and len(topic)<=250
def join_channel(user,channel_id):
    ch=get_channel(channel_id)
    if not ch:return None
    with tx() as c:
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
        role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],user['id'])).fetchone()
        already=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel_id,user['id'])).fetchone()
        if ch['is_private'] and not already: return False
        if not role:
            if w['join_mode']!='open': return False
            add_workspace_member(c,ch['workspace_id'],user['id'],'member')
        if role and role['role']=='guest': return False
        add_channel_member(c,channel_id,user['id'])
        if role is None:
            c.execute('INSERT OR IGNORE INTO channel_members(channel_id,user_id) VALUES(?,?)',(channel_id,user['id']))
    return True
def workspace_detail(user,slug,include_archived=False):
    w=get_workspace(slug)
    if not w or not member_role(w['id'],user['id']): return None
    c=connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (?=1 OR is_archived=0)',(w['id'],1 if include_archived else 0)).fetchall()
    rows=[r for r in rows if not r['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['id'],user['id'])).fetchone()]
    states=[]
    for r in rows:
        z=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(user['id'],r['id'])).fetchone(); last=z[0] if z else 0
        unread=mention=0
        for e in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(r['id'],last)).fetchall():
            try:
                mid=json.loads(e['payload']).get('message',{}).get('id')
                m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
                if m and m['author_id']!=user['id']:
                    unread+=1
                    if user['id'] in resolve_mentions(c,m['body'],r,m['author_id']): mention+=1
            except: pass
        states.append({'channel_id':r['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':mention})
    c.close(); return {'workspace':workspace_to_json(w),'channels':[channel_to_json(r) for r in rows],'read_state':states}
def set_archived(actor,channel_id,flag):
    ch=get_channel(channel_id)
    if not ch:return None
    if member_role(ch['workspace_id'],actor['id'])!='owner':return False
    with tx() as c:
        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(flag),channel_id))
        if bool(ch['is_archived']) != bool(flag): emit_event(c,channel_id,'channel.archived' if flag else 'channel.unarchived',{})
    return get_channel(channel_id)

def encode_cursor(kind,v):
    return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':int(v)}).encode()).decode().rstrip('=')
def decode_cursor(kind,s):
    try:
        raw=s+'='*((4-len(s)%4)%4); x=json.loads(base64.urlsafe_b64decode(raw))
        if not isinstance(x,dict) or x.get('k')!=kind or not isinstance(x.get('v'),int): raise ValueError()
        return x['v']
    except: raise ValueError()
def parse_paging(query,kind):
    if 'page' in query or 'offset' in query: raise ValueError()
    try: limit=int(query.get('limit','50'))
    except: raise ValueError()
    if limit<1 or limit>200: raise ValueError()
    cur=decode_cursor(kind,query['cursor']) if query.get('cursor') else None
    return limit,cur
def list_messages(channel_id,limit=50,cursor=None):
    c=connect()
    if cursor is None:r=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT ?',(channel_id,limit+1)).fetchall()
    else:r=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL AND id<? ORDER BY id DESC LIMIT ?',(channel_id,cursor,limit+1)).fetchall()
    c.close(); more=len(r)>limit; r=r[:limit]
    return [message_to_json(x) for x in r],(encode_cursor('messages',r[-1]['id']) if more and r else None)
def list_replies(parent_id,limit=50,cursor=None):
    c=connect()
    if cursor is None:r=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id ASC LIMIT ?',(parent_id,limit+1)).fetchall()
    else:r=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id ASC LIMIT ?',(parent_id,cursor,limit+1)).fetchall()
    c.close();more=len(r)>limit;r=r[:limit]
    return [message_to_json(x) for x in r],(encode_cursor('replies',r[-1]['id']) if more and r else None)

def pin_message(user_id,message_id):
    c=connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(message_id,)).fetchone(); c.close()
    if not m:return None
    with tx() as c:
        c.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(message_id,user_id,now_iso()))
        return c.execute('SELECT * FROM pins WHERE message_id=?',(message_id,)).fetchone()
def unpin_message(message_id):
    with tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(message_id,)); return True
def list_pins(channel_id,limit=50,cursor=None):
    c=connect()
    q='SELECT p.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.message_id DESC'
    args=[channel_id]
    if cursor is not None:q=q.replace('ORDER BY','AND p.message_id<? ORDER BY');args.append(cursor)
    rows=c.execute(q+' LIMIT ?',args+[limit+1]).fetchall(); out=[]
    for p in rows[:limit]:
        m=c.execute('SELECT * FROM messages WHERE id=?',(p['message_id'],)).fetchone()
        out.append({'message':message_to_json(m),'pinned_by':p['pinned_by'],'pinned_at':p['pinned_at']})
    c.close();return out,(encode_cursor('pins',rows[limit-1]['message_id']) if len(rows)>limit else None)

def resolve_mentions(c,body,channel,author_id):
    if not channel or not channel['workspace_id']: return []
    rows=c.execute("SELECT u.id,u.username FROM users u JOIN members m ON m.user_id=u.id WHERE m.workspace_id=?",(channel['workspace_id'],)).fetchall()
    ids={r['username']:r['id'] for r in rows}
    cm={r[0] for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],)).fetchall()}
    out=[]
    for x in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',body):
        if x=='channel': vals=cm
        elif x=='here': vals=cm & present_user_ids()
        else:
            g=c.execute('SELECT user_ids FROM groups_tbl WHERE workspace_id=? AND handle=?',(channel['workspace_id'],x)).fetchone()
            if g:
                vals=set(json.loads(g['user_ids'])) & cm
            else: vals={ids[x]} if x in ids and ids[x] in cm else set()
        out += [i for i in vals if i!=author_id and i not in out]
    return out
def group_to_json(r):
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'])}
def create_group(workspace_id,handle,name,user_ids,creator_id):
    with tx() as c:
        if c.execute('SELECT 1 FROM groups_tbl WHERE workspace_id=? AND handle=?',(workspace_id,handle)).fetchone(): raise ValueError('duplicate')
        for u in user_ids:
            if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(workspace_id,u)).fetchone(): raise ValueError('member')
        cur=c.execute('INSERT INTO groups_tbl(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(workspace_id,handle,name,creator_id,json.dumps(user_ids)))
        return c.execute('SELECT * FROM groups_tbl WHERE id=?',(cur.lastrowid,)).fetchone()
def update_group(workspace_id,handle,name=None,user_ids=None):
    with tx() as c:
        r=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=? AND handle=?',(workspace_id,handle)).fetchone()
        if not r:return None
        if name is None:name=r['name']
        if user_ids is None:user_ids=json.loads(r['user_ids'])
        for u in user_ids:
            if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(workspace_id,u)).fetchone(): raise ValueError('member')
        c.execute('UPDATE groups_tbl SET name=?,user_ids=? WHERE id=?',(name,json.dumps(user_ids),r['id']))
        return c.execute('SELECT * FROM groups_tbl WHERE id=?',(r['id'],)).fetchone()
def delete_group(workspace_id,handle):
    with tx() as c:
        c.execute('DELETE FROM groups_tbl WHERE workspace_id=? AND handle=?',(workspace_id,handle));return True

def present_user_ids():
    c=connect();r={x[0] for x in c.execute('SELECT user_id FROM presence').fetchall()};c.close();return r

def reactions_for(message_id):
    c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(message_id,)).fetchall(); c.close()
    out=[]
    for r in rows:
        x=next((z for z in out if z['emoji']==r['emoji']),None)
        if not x:x={'emoji':r['emoji'],'count':0,'user_ids':[]};out.append(x)
        x['user_ids'].append(r['user_id']);x['count']+=1
    return out
def react(user,message_id,emoji,add=True):
    c=connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(message_id,)).fetchone();c.close()
    if not m:return None
    with tx() as c:
        exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user['id'],emoji)).fetchone()
        if add:
            if exists:return reactions_for(message_id)
            c.execute('INSERT INTO reactions VALUES(?,?,?)',(message_id,user['id'],emoji))
            emit_event(c,m['channel_id'],'reaction.added',{'message_id':message_id,'emoji':emoji,'user_id':user['id']})
        else:
            if not exists:return reactions_for(message_id)
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user['id'],emoji))
            emit_event(c,m['channel_id'],'reaction.removed',{'message_id':message_id,'emoji':emoji,'user_id':user['id']})
    return reactions_for(message_id)

def file_to_json(r):
    return {'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}

def run_slash(user,channel,body):
    if body.startswith('//'): return {'body':body[1:]}
    if not body.startswith('/'): return {'body':body}
    parts=body.split(' ',1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
    if cmd=='/me':
        if not arg.strip(): raise ValueError()
        return {'body':body}
    if cmd=='/shrug': return {'body':(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'}
    if cmd in ('/topic','/archive','/unarchive','/invite'):
        return {'side':cmd,'arg':arg}
    raise ValueError()

def touch_presence(conn_id,user_id):
    with tx() as c:
        c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,strftime("%s","now"))',(conn_id,user_id))
def drop_presence(conn_id):
    with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))

def globals_after(since):
    c=connect(); rows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall();c.close()
    out=[]
    for r in rows:
        x=json.loads(r['payload']); x['_global_id']=r['id']; out.append(x)
    return out

def edit_message(user,message_id,body):
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not m:return ('missing',None)
        if m['deleted']:return ('deleted',None)
        if m['author_id']!=user['id']:return ('forbidden',None)
        t=now_iso();c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,t,message_id))
        m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        emit_event(c,m['channel_id'],'message.updated',{'message':message_to_json(m)})
        return ('ok',message_to_json(m))
def delete_message(user,message_id):
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not m:return ('missing',None)
        if m['deleted']:return ('already',None)
        owner=c.execute('SELECT owner_id FROM workspaces w JOIN channels ch ON ch.workspace_id=w.id WHERE ch.id=?',(m['channel_id'],)).fetchone()
        if m['author_id']!=user['id'] and (not owner or owner[0]!=user['id']):return ('forbidden',None)
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(message_id,))
        emit_event(c,m['channel_id'],'message.deleted',{'message_id':message_id})
        return ('ok',True)
