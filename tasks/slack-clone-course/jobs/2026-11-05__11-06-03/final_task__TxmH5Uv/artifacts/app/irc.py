import asyncio, core, shlex

clients={}
joined={}
def line(w,s):
    try:w.write((s+'\r\n').encode())
    except:pass
async def send_registration(w,nick):
    for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1 1'),('005','CHANTYPES=# PREFIX=(o)@')]:
        line(w,f':huddle {code} {nick} :{text}')
    line(w,f':huddle 422 {nick} :MOTD File is missing')
def find_channel(name):
    if not name.startswith('#') or '/' not in name:return None
    x=name[1:].split('/',1); w=core.get_workspace(x[0])
    if not w:return None
    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],x[1])).fetchone();c.close()
    return ch
async def handle(r,w):
    token=None; nick=None; user=None; registered=False; mine=set(); last={}; stop=False
    async def deliver():
        while not stop:
            if registered:
                for cid in list(mine):
                    for ev in core.events_after(cid,last.get(cid,core.head_event_id(cid))):
                        last[cid]=ev['event_id']
                        if ev.get('type')=='message.created':
                            m=ev.get('message') or {}; au=m.get('author') or {}
                            c=core.connect()
                            ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                            wsrow=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch and ch['workspace_id'] else None
                            c.close()
                            if ch and wsrow and au.get('id') != u['id']:
                                line(w,f':{au.get("username", "user")}!{au.get("username", "user")}@huddle PRIVMSG #{wsrow["slug"]}/{ch["name"]} :{m.get("body","")}')
            await asyncio.sleep(.05)
    delivery=asyncio.create_task(deliver())
    peer=w.get_extra_info('peername'); host=str(peer[0] if peer else 'localhost')
    try:
        while True:
            raw=await r.readline()
            if not raw:break
            text=raw.decode(errors='ignore').strip('\r\n')
            if not text:continue
            parts=text.split(' ',2); cmd=parts[0].upper(); rest=parts[1:]
            if cmd=='PASS':
                token=rest[0] if rest else ''
                if not core.user_for_token(token):line(w,':huddle 464 * :Password incorrect')
            elif cmd=='NICK':
                nn=rest[0] if rest else ''
                if nn in clients and clients[nn] is not w:
                    line(w,f':huddle 433 * {nn} :Nickname is already in use');continue
                nick=nn; clients[nick]=w
            elif cmd=='USER':
                user=rest[0] if rest else ''
            if not registered and token and nick and user and core.user_for_token(token):
                registered=True; joined[w]=set(); await send_registration(w,nick)
            if not registered: 
                if cmd in ('PASS','NICK','USER'):continue
                continue
            u=core.user_for_token(token)
            if cmd=='PING':
                arg=text.split(':',1)[1] if ':' in text else (rest[0] if rest else '')
                line(w,f':huddle PONG huddle :{arg}')
            elif cmd=='PONG': pass
            elif cmd=='JOIN':
                name=rest[0] if rest else ''; ch=find_channel(name)
                if not ch:line(w,f':huddle 403 {nick} {name} :No such channel');continue
                core.join_channel if False else None
                with core.tx() as c:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
                mine.add(ch['id']); joined[w].add(ch['id']); last[ch['id']]=core.head_event_id(ch['id'])
                line(w,f':{nick}!{u["username"]}@{host} JOIN {name}')
                c=core.connect(); names=c.execute('SELECT u.username FROM channel_members m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
                line(w,f':huddle 353 {nick} = {name} :{" ".join(x["username"] for x in names)}')
                line(w,f':huddle 366 {nick} {name} :End of NAMES list')
            elif cmd=='PRIVMSG':
                if len(rest)<2:continue
                name=rest[0]; body=rest[1]
                if body.startswith(':'):body=body[1:]
                ch=find_channel(name)
                if not ch or ch['id'] not in mine:continue
                core.create_message(u,ch['id'],body)
            elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'): 
                if cmd=='NAMES' and rest:
                    name=rest[0];ch=find_channel(name)
                    if ch:
                        c=core.connect();ns=c.execute('SELECT u.username FROM channel_members m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
                        line(w,f':huddle 353 {nick} = {name} :{" ".join(x["username"] for x in ns)}');line(w,f':huddle 366 {nick} {name} :End of NAMES list')
            elif cmd=='QUIT':break
            else: line(w,f':huddle 421 {nick} {cmd} :Unknown command')
    finally:
        stop=True
        delivery.cancel()
        if nick and clients.get(nick) is w:del clients[nick]
        try:w.close();await w.wait_closed()
        except:pass
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s:await s.serve_forever()
asyncio.run(main())
