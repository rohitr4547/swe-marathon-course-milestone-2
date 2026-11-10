import asyncio, re, json, time
import core
clients=[]
nicks={}
def line(w,s):
    try:w.write((s+'\r\n').encode())
    except:pass
async def handle(reader,w):
    cl={'w':w,'user':None,'nick':None,'channels':set(),'seen':{}}
    clients.append(cl); registered=False; closed=False; user_sent=False
    async def deliver():
        while not closed:
            await asyncio.sleep(.05)
            if not registered: continue
            c=core.conn()
            for cid in list(cl['channels']):
                n=cl['seen'].get(cid,0)
                for row in c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,n)):
                    f=json.loads(row['payload']);cl['seen'][cid]=row['event_id']
                    if f.get('type')=='message.created' and f.get('message',{}).get('author_id')!=cl['user']:
                        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                        ws=c.execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                        name='#'+ws['slug']+'/'+ch['name']; au=c.execute('SELECT username FROM users WHERE id=?',(f['message']['author_id'],)).fetchone()
                        line(w,f":{au['username']}!{au['username']}@host PRIVMSG {name} :{f['message']['body']}")
    task=asyncio.create_task(deliver())
    try:
        while True:
            raw=await reader.readline()
            if not raw: break
            txt=raw.decode(errors='ignore').strip(); parts=txt.split(); cmd=parts[0].upper() if parts else ''
            if cmd=='PASS' and len(parts)>1:
                q=core.conn().execute('SELECT user_id FROM tokens WHERE token=?',(parts[1],)).fetchone()
                if not q: line(w,':irc 464 * :Password incorrect'); continue
                cl['user']=q[0]
            elif cmd=='NICK' and len(parts)>1:
                nick=parts[1]
                if nick in nicks and nicks[nick] is not cl:
                    line(w,f':irc 433 * {nick} :Nickname in use'); continue
                if cl['nick'] and cl['nick'] in nicks: del nicks[cl['nick']]
                cl['nick']=nick;nicks[nick]=cl
                if cl['user'] and user_sent and not registered:
                    registered=True
                    for code,text in [('001','Welcome'),('002','Your host'),('003','Server created'),('004','irc'),('005','CHANTYPES=#'),('422','MOTD File is missing')]: line(w,f':irc {code} {cl["nick"]} :{text}')
            elif cmd=='USER' and cl['user'] and not registered:
                user_sent=True
                if cl['nick']:
                    registered=True
                    for code,text in [('001','Welcome'),('002','Your host'),('003','Server created'),('004','irc'),('005','CHANTYPES=#'),('422','MOTD File is missing')]: line(w,f':irc {code} {cl["nick"]} :{text}')
            elif cmd=='PING':
                line(w,':irc PONG '+(txt.split(':',1)[1] if ':' in txt else (parts[1] if len(parts)>1 else '')))
            elif cmd=='JOIN' and len(parts)>1:
                name=parts[1]; m=re.fullmatch(r'#([^/]+)/([^/]+)',name)
                c=core.conn(); ch=None
                if m: ch=c.execute('SELECT ch.*,w.slug FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(m[1],m[2])).fetchone()
                if not ch: line(w,f':irc 403 {cl["nick"]} {name} :No such channel'); continue
                cid=ch['id'];cl['channels'].add(cid);cl['seen'][cid]=ch['last_event_id']
                line(w,f':{cl["nick"]}!{cl["nick"]}@host JOIN {name}')
                names=[x['username'] for x in c.execute('SELECT u.username FROM users u JOIN members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))]
                line(w,f':irc 353 {cl["nick"]} = {name} :{" ".join(names)}');line(w,f':irc 366 {cl["nick"]} {name} :End of NAMES list')
            elif cmd=='PRIVMSG' and len(parts)>2:
                target=parts[1]; body=txt.split(' :',1)[1] if ' :' in txt else ''
                m=re.fullmatch(r'#([^/]+)/([^/]+)',target);c=core.conn();ch=None
                if m:ch=c.execute('SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(m[1],m[2])).fetchone()
                if ch:
                    c.execute('BEGIN IMMEDIATE'); cur=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at,mentions) VALUES(?,?,?,?,?)',(ch['id'],cl['user'],body,core.now_iso(),'[]'));mid=cur.lastrowid;row=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();core.emit(c,ch['id'],'message.created',{'message':core.msg_json(row,c)});c.commit()
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'): pass
            elif cmd=='QUIT': break
            else: line(w,f':irc 421 {cl["nick"] or "*"} {cmd} :Unknown command')
    finally:
        closed=True;task.cancel();clients.remove(cl) if cl in clients else None
        if cl['nick'] in nicks and nicks[cl['nick']] is cl: del nicks[cl['nick']]
        w.close();await w.wait_closed()
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s: await s.serve_forever()
asyncio.run(main())
