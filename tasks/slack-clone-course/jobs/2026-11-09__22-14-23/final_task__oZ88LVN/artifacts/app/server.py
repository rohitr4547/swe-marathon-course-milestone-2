import asyncio,json,argparse,re,sqlite3
from aiohttp import web,WSMsgType
import core
def j(data,status=200): return web.json_response(data,status=status)
def err(msg,status): return j({'error':msg},status)
async def body(r):
    try:return await r.json()
    except:return None
def uid(r):
    h=r.headers.get('Authorization','')
    if not h.startswith('Bearer '):return None
    x=core.conn().execute('SELECT user_id FROM tokens WHERE token=?',(h[7:],)).fetchone()
    return x[0] if x else None
def need(r):
    x=uid(r)
    if not x: raise web.HTTPUnauthorized()
    return x
def tx():
    c=core.conn(); c.execute('BEGIN IMMEDIATE'); return c
async def register(r):
    b=await body(r)
    if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err('invalid',400)
    c=tx()
    try:
        cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.pw(b['password']),b.get('display_name') or b['username']))
        u=cur.lastrowid; t=core.secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,u)); c.commit()
        return j({'user':core.user_json(c.execute('SELECT * FROM users WHERE id=?',(u,)).fetchone()),'token':t},201)
    except sqlite3.IntegrityError:c.rollback();return err('duplicate',409)
async def login(r):
    b=await body(r); c=core.conn()
    if not isinstance(b,dict):return err('invalid',400)
    u=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),core.pw(b.get('password','')))).fetchone()
    if not u:return err('invalid credentials',401)
    t=core.secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id'],));return j({'user':core.user_json(u),'token':t})
async def me(r):
    x=uid(r)
    if not x:return err('unauthorized',401)
    c=core.conn();return j({'user':core.user_json(c.execute('SELECT * FROM users WHERE id=?',(x,)).fetchone())})
async def health(r):return j({'status':'ok','node_id':int(r.app['node'])})
async def create_ws(r):
    u=need(r);b=await body(r)
    if not isinstance(b,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return err('invalid',400)
    c=tx()
    try:
        cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',b['slug']),u));w=cur.lastrowid
        c.execute('INSERT INTO wmembers VALUES(?,?,?)',(u,w,'owner'));cur=c.execute("INSERT INTO channels(workspace_id,name) VALUES(?, 'general')",(w,));ch=cur.lastrowid;c.execute('INSERT INTO members VALUES(?,?,?)',(u,ch,'owner'));c.commit()
        return j({'workspace':core.ws_json(c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone()),'general_channel':core.ch_json(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())},201)
    except sqlite3.IntegrityError:c.rollback();return err('duplicate',409)
async def workspaces(r):
    u=need(r);c=core.conn(); rows=c.execute('SELECT w.* FROM workspaces w JOIN wmembers m ON m.workspace_id=w.id WHERE m.user_id=?',(u,)).fetchall();return j({'workspaces':[core.ws_json(x) for x in rows]})
async def workspace(r):
    u=need(r);c=core.conn();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
    if not w:return err('not found',404)
    rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();return j({'workspace':core.ws_json(w),'channels':[core.ch_json(x) for x in rows],'read_state':{}})
async def create_channel(r):
    u=need(r);b=await body(r);c=tx();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(r.match_info['slug'],)).fetchone()
    if not w:return err('not found',404)
    if not isinstance(b,dict) or not isinstance(b.get('name'),str):return err('invalid',400)
    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone(): c.rollback(); return err('duplicate',409)
    try:cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic','')));i=cur.lastrowid;c.execute('INSERT INTO members VALUES(?,?,?)',(u,i,'member'));c.commit();return j({'channel':core.ch_json(c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone())},201)
    except sqlite3.IntegrityError:c.rollback();return err('duplicate',409)
async def patch_channel(r):
    u=need(r); b=await body(r); c=tx(); ch=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
    if not ch:c.rollback();return err('not found',404)
    if not isinstance(b,dict) or not isinstance(b.get('topic'),str) or len(b['topic'])>250:c.rollback();return err('invalid',400)
    c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']));c.commit()
    return j({'channel':core.ch_json(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
async def join(r):
    u=need(r);c=tx();x=c.execute('SELECT * FROM channels WHERE id=?',(r.match_info['id'],)).fetchone()
    if not x:return err('not found',404)
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(u,x['id'],'member'))
    if x['workspace_id']: c.execute('INSERT OR IGNORE INTO wmembers VALUES(?,?,?)',(u,x['workspace_id'],'member'))
    c.commit();return j({'channel':core.ch_json(x)})
async def messages(r):
    u=need(r);cid=int(r.match_info['id']);c=core.conn();x=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not x:return err('not found',404)
    if r.method=='GET':
        rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(cid,)).fetchall();return j({'messages':[core.msg_json(z,c) for z in rows],'next_cursor':None})
    b=await body(r)
    if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err('invalid',400)
    c.execute('BEGIN IMMEDIATE')
    ch=x; wrow=c.execute('SELECT workspace_id FROM channels WHERE id=?',(cid,)).fetchone()
    mids={z['id'] for z in c.execute('SELECT u.id FROM users u JOIN wmembers m ON m.user_id=u.id WHERE m.workspace_id=?',(wrow['workspace_id'],)).fetchall()} if wrow['workspace_id'] else set()
    mentions=[]
    for z in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',b['body']):
        if z=='channel': mentions += [q['user_id'] for q in c.execute('SELECT user_id FROM members WHERE channel_id=? AND user_id!=?',(cid,u))]
        else:
            q=c.execute('SELECT id FROM users WHERE username=?',(z,)).fetchone()
            if q and q['id'] in mids and q['id']!=u: mentions.append(q['id'])
    mentions=list(dict.fromkeys(mentions))
    cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,u,b['body'],b.get('parent_id'),core.now_iso(),json.dumps(mentions)));i=cur.lastrowid;row=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone();core.emit(c,cid,'message.created',{'message':core.msg_json(row,c)});c.commit();return j({'message':core.msg_json(row,c)},201)
async def edit(r):
    u=uid(r)
    if not u:return err('unauthorized',401)
    c=tx();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone()
    if not m:c.rollback();return err('not found',404)
    b=await body(r)
    if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():c.rollback();return err('invalid',400)
    if m['deleted']:c.rollback();return err('deleted',409)
    if m['author_id']!=u:c.rollback();return err('forbidden',403)
    mentions=[]
    wrow=c.execute('SELECT workspace_id FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
    mids={z['id'] for z in c.execute('SELECT u.id FROM users u JOIN wmembers m ON m.user_id=u.id WHERE m.workspace_id=?',(wrow['workspace_id'],)).fetchall()} if wrow['workspace_id'] else set()
    for z in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',b['body']):
        q=c.execute('SELECT id FROM users WHERE username=?',(z,)).fetchone()
        if q and q['id'] in mids and q['id']!=u: mentions.append(q['id'])
    c.execute('UPDATE messages SET body=?,mentions=?,edited_at=? WHERE id=?',(b['body'],json.dumps(list(dict.fromkeys(mentions))),core.now_iso(),m['id']))
    m=c.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone();core.emit(c,m['channel_id'],'message.updated',{'message':core.msg_json(m,c)});c.commit()
    return j({'message':core.msg_json(m,c)})
async def delete(r):
    u=uid(r)
    if not u:return err('unauthorized',401)
    c=tx();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone()
    if not m:c.rollback();return err('not found',404)
    w=c.execute('SELECT * FROM workspaces w JOIN channels ch ON ch.workspace_id=w.id WHERE ch.id=?',(m['channel_id'],)).fetchone()
    if m['deleted']:c.commit();return j({'deleted':True})
    if m['author_id']!=u and (not w or w['owner_id']!=u):c.rollback();return err('forbidden',403)
    c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m['id'],));c.execute('UPDATE messages SET deleted=1 WHERE parent_id=?',(m['id'],));c.execute('DELETE FROM reactions WHERE message_id=?',(m['id'],));core.emit(c,m['channel_id'],'message.deleted',{'message_id':m['id']});c.commit();return j({'deleted':True})
async def replies(r):
    u=need(r);c=core.conn();rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(r.match_info['id'],)).fetchall();return j({'replies':[core.msg_json(x,c) for x in rows],'next_cursor':None})
async def reaction(r):
    u=need(r);b=await body(r);emoji=b.get('emoji') if isinstance(b,dict) else None
    if not isinstance(emoji,str) or not emoji:return err('invalid',400)
    c=tx();m=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone()
    if not m:return err('not found',404)
    if r.method=='POST':c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(m['id'],u,emoji))
    else:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u,emoji))
    rows=c.execute('SELECT emoji,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(m['id'],)).fetchall();c.commit()
    return j({'reactions':[{'emoji':x['emoji'],'count':x['n']} for x in rows]})
async def ws(r):
    u=uid(r)
    if not u:
        tok=r.query.get('token','')
        if tok.startswith('Bearer '): tok=tok[7:]
        if tok: 
            q=core.conn().execute('SELECT user_id FROM tokens WHERE token=?',(tok,)).fetchone(); u=q[0] if q else None
    if not u:return err('unauthorized',401)
    sock=web.WebSocketResponse();await sock.prepare(r); subs=set();seen={}
    async def poll():
        while not sock.closed:
            c=core.conn()
            for cid in list(subs):
                n=seen.get(cid,0);rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,n)).fetchall()
                for x in rows:await sock.send_str(x['payload']);seen[cid]=x['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    try:
        async for msg in sock:
            if msg.type==WSMsgType.TEXT:
                x=json.loads(msg.data)
                if x.get('type') in ('subscribe','resume'):
                    cid=int(x['channel_id']);subs.add(cid); head=core.conn().execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()[0]; seen[cid]=head if x['type']=='subscribe' else int(x.get('since_event_id',0))
                    await sock.send_json({'type':'subscribed' if x['type']=='subscribe' else 'resumed','channel_id':cid,'head_event_id':head})
    finally:task.cancel()
    return sock
def main():
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);a=p.parse_args();core.init_db()
    app=web.Application();app['node']=a.node
    app.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'));app.router.add_get('/app.js',lambda r:web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'}));app.router.add_get('/api/health',health);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
    app.router.add_post('/api/workspaces',create_ws);app.router.add_get('/api/workspaces',workspaces);app.router.add_get('/api/workspaces/{slug}',workspace);app.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    app.router.add_post('/api/channels/{id}/join',join);app.router.add_patch('/api/channels/{id}',patch_channel);app.router.add_route('*','/api/channels/{id}/messages',messages);app.router.add_get('/api/messages/{id}/replies',replies)
    app.router.add_patch('/api/messages/{id}',edit);app.router.add_delete('/api/messages/{id}',delete);app.router.add_route('*','/api/messages/{id}/reactions',reaction)
    app.router.add_get('/api/ws',ws)
    web.run_app(app,host='127.0.0.1',port=8000+a.node)
if __name__=='__main__':main()

