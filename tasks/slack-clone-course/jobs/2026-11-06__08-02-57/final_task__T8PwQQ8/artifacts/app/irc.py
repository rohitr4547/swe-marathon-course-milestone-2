import asyncio, core, re
NICKS=set()
LOCK=asyncio.Lock()
def send(w,line): w.write((line+'\r\n').encode())
def channel_name(cid):
    c=core.connect(); r=c.execute('SELECT w.slug,ch.name FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE ch.id=?',(cid,)).fetchone(); c.close()
    return '#'+r['slug']+'/'+r['name'] if r else ''
async def handle(reader,writer):
    nick=None; username=None; token=None; user=None; registered=False
    joined={}; cursors={}; skip=set()
    async def flush(): await writer.drain()
    async def register():
        nonlocal registered,user
        if registered or not nick or not username:return None
        user=core.user_for_token(token)
        if not user:
            send(writer,':huddle 464 * :Password incorrect'); await flush(); await asyncio.sleep(.05); writer.close(); await writer.wait_closed(); return False
        async with LOCK:
            if nick in NICKS:
                send(writer,f':huddle 433 * {nick} :Nickname is already in use'); await flush(); return True
            NICKS.add(nick)
        registered=True
        for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1.0 oi'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]:
            send(writer,f':huddle {code} :{text}' if code=='422' else f':huddle {code} {nick} :{text}')
        await flush(); return True
    async def deliver():
        try:
            while True:
                for cid,last in list(cursors.items()):
                    for e in core.events_after(cid,last):
                        cursors[cid]=e['event_id']
                        if e['event_id'] in skip:
                            skip.discard(e['event_id']); continue
                        if e['type'] not in ('message.created','message.reply'): continue
                        m=e.get('message') or {}
                        author=m.get('author') or {}
                        body=m.get('body','')
                        target=channel_name(cid)
                        if target:
                            an=author.get('username','user')
                            send(writer,f':{an}!{an}@huddle PRIVMSG {target} :{body}')
                await flush()
                await asyncio.sleep(.05)
        except (asyncio.CancelledError,ConnectionError): pass
        except Exception: pass
    task=None
    try:
        while True:
            raw=await reader.readline()
            if not raw: break
            line=raw.decode(errors='replace').strip('\r\n')
            bits=line.split(' ',1); cmd=bits[0].upper(); rest=bits[1] if len(bits)>1 else ''
            if cmd=='PASS': token=rest[1:] if rest.startswith(':') else rest.strip()
            elif cmd=='NICK':
                new=rest.lstrip(':').split()[0] if rest else ''
                if registered:
                    async with LOCK:
                        if new in NICKS and new!=nick:
                            send(writer,f':huddle 433 * {new} :Nickname is already in use'); await flush(); continue
                        NICKS.discard(nick); NICKS.add(new)
                    nick=new
                else: nick=new
                result=await register()
                if result is False: break
                if registered and task is None: task=asyncio.create_task(deliver())
            elif cmd=='USER':
                p=rest.split(); username=p[0] if p else ''
                result=await register()
                if result is False: break
                if registered and task is None: task=asyncio.create_task(deliver())
            elif not registered: continue
            elif cmd=='PING':
                tok=rest[1:] if rest.startswith(':') else rest; send(writer,f':huddle PONG huddle :{tok}'); await flush()
            elif cmd=='PONG': pass
            elif cmd=='QUIT': break
            elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'):
                if cmd=='NAMES' and rest:
                    target=rest.split()[0]; send(writer,f':huddle 353 {nick} = {target} :{nick}'); send(writer,f':huddle 366 {nick} {target} :End of /NAMES list'); await flush()
            elif cmd=='JOIN':
                target=rest.split()[0] if rest else ''; m=re.fullmatch(r'#([a-z0-9-]+)/([a-z0-9-]+)',target)
                if not m:
                    send(writer,f':huddle 403 {nick} {target} :No such channel'); await flush(); continue
                slug,name=m.groups(); w=core.get_workspace(slug)
                c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone() if w else None; c.close()
                if not ch:
                    send(writer,f':huddle 403 {nick} {target} :No such channel'); await flush(); continue
                try:
                    with core.tx() as c:
                        member=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],user['id'])).fetchone()
                        if member and member['role']=='guest': raise PermissionError()
                        if not member:
                            if w['join_mode']!='open' or ch['is_private']: raise PermissionError()
                            core.add_workspace_member(c,w['id'],user['id'])
                        if ch['is_private'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],user['id'])).fetchone(): raise PermissionError()
                        c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],user['id']))
                    joined[target]=ch['id']; cursors[ch['id']]=core.head_event_id(ch['id'])
                except PermissionError:
                    send(writer,f':huddle 403 {nick} {target} :No such channel'); await flush(); continue
                prefix=f':{nick}!{username}@huddle'; send(writer,f'{prefix} JOIN {target}')
                c=core.connect(); names=[x['username'] for x in c.execute('SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall()]; c.close()
                send(writer,f':huddle 353 {nick} = {target} :{" ".join(names)}'); send(writer,f':huddle 366 {nick} {target} :End of /NAMES list'); await flush()
            elif cmd=='PRIVMSG':
                parts=rest.split(' ',1); target=parts[0] if parts else ''; body=parts[1] if len(parts)>1 else ''
                if body.startswith(':'): body=body[1:]
                cid=joined.get(target)
                if cid and body:
                    with core.tx() as c:
                        c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(cid,user['id'],body,core.now_iso()))
                        mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                        c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
                        core.emit_event(c,cid,'message.created',{'message':{'id':mid,'author':core.user_to_json(user),'body':body,'channel_id':cid}})
                    skip.add(core.head_event_id(cid))
            else:
                send(writer,f':huddle 421 {nick} {cmd} :Unknown command'); await flush()
    finally:
        if task: task.cancel()
        if nick:
            async with LOCK:NICKS.discard(nick)
        writer.close()
        try: await writer.wait_closed()
        except: pass
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s: await s.serve_forever()
if __name__=='__main__': asyncio.run(main())
