import asyncio, core, re
clients=set()
async def handle(reader,writer):
    nick=None; user=None; authed=False; joined=set(); seen={}; stop=False
    async def deliver():
        while not stop:
            for name in list(joined):
                slug,cname=name[1:].split('/',1); w=core.get_workspace(slug); c=core.connect(); ch=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone() if w else None
                if ch:
                    rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch['id'],seen.get(ch['id'],0))).fetchall()
                    for row in rows:
                        import json
                        f=json.loads(row['payload']); seen[ch['id']]=row['event_id']
                        if f.get('type') in ('message.created','message.reply'):
                            m=f.get('message',{}); a=m.get('author',{}).get('username','user')
                            await send(f':{a}!user@host PRIVMSG {name} :{m.get("body","")}')
                c.close()
            await asyncio.sleep(.05)
    async def send(x): writer.write((x+'\r\n').encode()); await writer.drain()
    task=asyncio.create_task(deliver())
    try:
        while True:
            line=await reader.readline()
            if not line: break
            line=line.decode(errors='ignore').strip()
            if not line: continue
            parts=line.split(' ',2); cmd=parts[0].upper(); arg=parts[1] if len(parts)>1 else ''; rest=parts[2] if len(parts)>2 else ''
            if cmd=='PASS':
                tok=arg
                if core.user_for_token(tok): authed=True
                else: await send(':huddle 464 * :Password incorrect')
            elif cmd=='NICK':
                nick=arg
                if any(x[0]==nick for x in clients): await send(f':huddle 433 * {nick} :Nickname in use')
            elif cmd=='USER': user=arg
            elif cmd=='PING':
                tok=(rest if rest else arg).lstrip(':'); await send(f':huddle PONG :{tok}')
            elif cmd=='QUIT': break
            elif cmd in ('NAMES','WHO','LIST','MODE','TOPIC'):
                await send(f':huddle 331 {nick or "*"} {arg} :No topic' if cmd=='TOPIC' else f':huddle 323 {nick or "*"} :End of LIST' if cmd=='LIST' else f':huddle 221 {nick or "*"} :Mode is')

            elif cmd=='JOIN':
                if not authed: await send(f':huddle 451 {nick or "*"} :You have not registered'); continue
                name=arg
                if not re.match(r'^#[^/]+/[^/]+$',name): await send(f':huddle 403 {nick} {name} :No such channel'); continue
                slug,cname=name[1:].split('/',1); w=core.get_workspace(slug)
                c=core.connect(); real=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone() if w else None; c.close()
                if not real: await send(f':huddle 403 {nick} {name} :No such channel'); continue
                joined.add(name); seen[real['id']]=core.connect().execute('SELECT last_event_id FROM channels WHERE id=?',(real['id'],)).fetchone()['last_event_id']; await send(f':{nick}!user@host JOIN :{name}'); await send(f':huddle 353 {nick} = {name} :{nick}'); await send(f':huddle 366 {nick} {name} :End of NAMES')
            elif cmd=='PRIVMSG':
                if not authed: await send(f':huddle 451 {nick} :You have not registered')
                else:
                    name=arg; text=rest.lstrip(':'); slug,cname=name[1:].split('/',1) if name.startswith('#') and '/' in name else ('','')
                    w=core.get_workspace(slug); c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone() if w else None
                    if not ch or name not in joined: await send(f':huddle 403 {nick} {name} :No such channel')
                    else:
                        with core.tx() as tx:
                            q=tx.execute('INSERT INTO messages(channel_id,author_id,body,created_at) SELECT ?,u.id,?,? FROM users u WHERE u.username=?',(ch['id'],text,core.now_iso(),nick))
                            m=tx.execute('SELECT * FROM messages WHERE id=?',(q.lastrowid,)).fetchone()
                            if m:
                                core.emit_event(tx,ch['id'],'message.created',{'message':{'id':m['id'],'channel_id':m['channel_id'],'author_id':m['author_id'],'body':m['body'],'parent_id':None,'reply_count':0,'created_at':m['created_at'],'edited_at':None,'files':[],'reactions':[],'mentions':[]}})
                                seen[ch['id']]=tx.execute('SELECT last_event_id FROM channels WHERE id=?',(ch['id'],)).fetchone()['last_event_id']
                        c.close()
            elif cmd not in ('CAP','PONG'): await send(f':huddle 421 {nick or "*"} {cmd} :Unknown command')
            if authed and nick and user and not any(x[0]==nick and x[1] is writer for x in clients):
                clients.add((nick,writer))
                for n in ('001','002','003','004','005'): await send(f':huddle {n} {nick} :Welcome')
                await send(f':huddle 422 {nick} :MOTD File is missing')
    finally:
        stop=True; task.cancel()
        for x in list(clients):
            if x[1] is writer: clients.remove(x)
        writer.close(); await writer.wait_closed()
async def main():
    await asyncio.start_server(handle,'0.0.0.0',6667)
    await asyncio.Event().wait()
asyncio.run(main())
