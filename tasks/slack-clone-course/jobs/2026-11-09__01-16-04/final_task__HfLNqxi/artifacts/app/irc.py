import asyncio, core, re
NICKS=set(); CLIENTS=[]
def line(s): return s+'\r\n'
async def send(w,s): w.write(line(s).encode()); await w.drain()
async def find_channel(name):
    if not name.startswith('#') or '/' not in name[1:]: return None
    slug,cname=name[1:].split('/',1); ws=core.get_workspace(slug)
    if not ws:return None
    c=core.connect(); r=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],cname)).fetchone();c.close()
    return r
async def handle(reader,writer):
    token=None; nick=None; user=None; registered=False; joined={}; sent={}; CLIENTS.append((writer,lambda: nick,lambda: user,joined,sent))
    async def deliver():
        while not writer.is_closing():
            for name,cid in list(joined.items()):
                since=sent.get(cid,0)
                for ev in core.events_after(cid,since):
                    sent[cid]=ev['event_id']
                    if ev.get('type') not in ('message.created','message.reply'): continue
                    msg=ev.get('message') or {}
                    if msg.get('author_id')==user['id']: continue
                    au=msg.get('author') or {}
                    await send(writer,f':{au.get("username","user")}!{au.get("username","user")}@huddle PRIVMSG {name} :{msg.get("body","")}')
            await asyncio.sleep(.05)
    delivery_task=None
    try:
        while True:
            raw=await reader.readline()
            if not raw:break
            try: text=raw.decode(errors='ignore').strip('\r\n'); parts=text.split(' ',1); cmd=parts[0].upper(); rest=parts[1] if len(parts)>1 else ''
            except: continue
            if cmd=='PASS': token=rest.strip()
            elif cmd=='NICK':
                n=rest.strip().split(' ',1)[0]
                if n in NICKS and n!=nick:
                    await send(writer,f':huddle 433 * {n} :Nickname is already in use');continue
                if nick:NICKS.discard(nick)
                nick=n;NICKS.add(nick)
            elif cmd=='USER':
                user=core.user_for_token(token)
                if nick and not user:
                    await send(writer,f':huddle 464 {nick} :Password incorrect'); break
            elif cmd=='QUIT':break
            elif cmd=='PING': await send(writer,':huddle PONG huddle :'+rest.lstrip(':'))
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'): continue
            elif not registered and cmd not in ('PASS','NICK','USER'):
                await send(writer,f':huddle 421 {nick or "*"} {cmd} :Unknown command');continue
            elif registered and cmd not in ('PASS','NICK','USER','JOIN','PRIVMSG','PING','PONG','NAMES','WHO','LIST','TOPIC','MODE','QUIT'):
                await send(writer,f':huddle 421 {nick} {cmd} :Unknown command');continue
            if not registered and nick and user:
                registered=True
                for code,msg in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','Server created'),('004','huddle 1.0 o o'),('005','CHANTYPES=#'),('422','MOTD File is missing')]:
                    await send(writer,f':huddle {code} {nick} :{msg}')
            if cmd=='PRIVMSG' and registered:
                bits=rest.split(' ',1)
                name=bits[0].strip() if bits else ''
                body=bits[1].lstrip(':') if len(bits)>1 else ''
                if name in joined and body:
                    core.create_message(user,joined[name],body)
                continue
            if cmd=='JOIN' and registered:
                name=rest.split(',')[0].strip(); ch=await find_channel(name)
                if not ch or not core.join_channel(user,ch['id']):
                    await send(writer,f':huddle 403 {nick} {name} :No such channel');continue
                joined[name]=ch['id']; sent.setdefault(ch['id'],core.head_event_id(ch['id'])); prefix=f':{nick}!{user["username"]}@huddle'
                c=core.connect(); names=[x[0] for x in c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall()];c.close()
                if delivery_task is None: delivery_task=asyncio.create_task(deliver())
                await send(writer,f'{prefix} JOIN {name}')
                await send(writer,f':huddle 353 {nick} = {name} :{" ".join(names)}')
                await send(writer,f':huddle 366 {nick} {name} :End of /NAMES list')
    finally:
        if nick:NICKS.discard(nick)
        if (writer,lambda: nick,lambda: user,joined,sent) in CLIENTS: pass
        if delivery_task: delivery_task.cancel()
        writer.close()
        try: await writer.wait_closed()
        except (Exception,): pass
async def main():
    server=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with server: await server.serve_forever()
if __name__=='__main__': asyncio.run(main())
