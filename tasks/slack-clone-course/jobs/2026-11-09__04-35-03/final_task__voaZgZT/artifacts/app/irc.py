import asyncio, re, core
clients={}
async def deliver(conn):
    w, nick, joined, sent, cursors = conn
    try:
        while True:
            for cid,name in list(joined.items()):
                c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,cursors.get(cid,0))).fetchall(); c.close()
                for row in rows:
                    cursors[cid]=row['event_id']
                    if row['event_id'] in sent: continue
                    try:
                        frame=__import__('json').loads(row['payload'])
                        if frame.get('type') not in ('message.created','message.reply'): continue
                        m=frame.get('message') or {}; a=m.get('author') or {}
                        await send(w,f':{a.get("username","user")}!{a.get("username","user")}@huddle PRIVMSG {name} :{m.get("body","")}\r\n')
                    except Exception: pass
            await asyncio.sleep(.05)
    except asyncio.CancelledError: pass
def line(code,nick,params,text): return f':huddle {code} {nick} {params} :{text}\r\n'
async def send(w,x): w.write(x.encode()); await w.drain()
async def handle(r,w):
    token=None; nick=None; username=None; user=None; registered=False; joined={}; sent=set(); cursors={}; delivery=None
    try:
        while True:
            raw=await r.readline()
            if not raw: break
            msg=raw.decode(errors='ignore').rstrip('\r\n')
            if not msg: continue
            bits=msg.split(' ',1); cmd=bits[0].upper(); rest=bits[1] if len(bits)>1 else ''
            args=rest.split()
            if cmd=='PASS': token=rest.strip()
            elif cmd=='NICK':
                proposed=args[0] if args else ''
                if proposed in clients and clients[proposed] is not w:
                    await send(w,line('433','*',proposed,'Nickname is already in use')); continue
                if nick in clients: clients.pop(nick,None)
                nick=proposed
            elif cmd=='USER':
                username=(args[0] if args else 'irc')
            elif cmd=='QUIT':
                break
            elif cmd=='PING':
                await send(w,':huddle PONG huddle :'+rest.lstrip(':')+'\r\n')
            elif cmd=='PONG': pass
            elif not registered and cmd not in ('PASS','NICK','USER'):
                await send(w,line('421',nick or '*',cmd,'Unknown command'))
            if not registered and nick and username is not None:
                user=core.user_for_token(token)
                if not user:
                    await send(w,line('464',nick,'','Password incorrect')); break
                if nick in clients and clients[nick] is not w:
                    continue
                clients[nick]=w; registered=True
                for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1 1'),('005','CHANTYPES=#')]:
                    await send(w,line(code,nick,'',text))
                await send(w,line('422',nick,'','MOTD File is missing'))
                continue
            if not registered: continue
            if cmd=='JOIN':
                name=args[0] if args else ''
                m=re.fullmatch(r'#([^/]+)/([^/]+)',name)
                if not m: await send(w,line('403',nick,name,'No such channel')); continue
                ws=core.get_workspace(m.group(1)); ch=None
                if ws:
                    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],m.group(2))).fetchone(); c.close()
                if not ch:
                    await send(w,line('403',nick,name,'No such channel')); continue
                try: core.join_channel(user,ch['id']) if hasattr(core,'join_channel') else None
                except: await send(w,line('403',nick,name,'No such channel')); continue
                joined[ch['id']]=name
                c0=core.connect(); h0=c0.execute('SELECT last_event_id FROM channels WHERE id=?',(ch['id'],)).fetchone(); c0.close(); cursors[ch['id']]=h0['last_event_id'] if h0 else 0
                if delivery is None:
                    delivery=asyncio.create_task(deliver((w,nick,joined,sent,cursors)))
                c=core.connect(); rows=c.execute('SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall(); c.close()
                names=' '.join(x['username'] for x in rows)
                await send(w,f':{nick}!{user["username"]}@huddle JOIN {name}\r\n')
                await send(w,line('353',nick,f'= {name}',names)); await send(w,line('366',nick,name,'End of /NAMES list'))
            elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'): pass
            elif cmd=='PRIVMSG':
                if len(args)<2: continue
                target=args[0]; body=rest.split(' :',1)[1] if ' :' in rest else ' '.join(args[1:])
                m=re.fullmatch(r'#([^/]+)/([^/]+)',target)
                if m:
                    ws=core.get_workspace(m.group(1)); ch=None
                    if ws:
                        c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],m.group(2))).fetchone(); c.close()
                    if ch:
                        row=core.create_message(user,ch['id'],body)
                        c=core.connect(); er=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch['id'],)).fetchone(); c.close()
                        if er: sent.add(er['last_event_id'])
            elif cmd not in ('PASS','NICK','USER','PING','PONG'): await send(w,line('421',nick,cmd,'Unknown command'))
    finally:
        if nick and clients.get(nick) is w: clients.pop(nick,None)
        if delivery: delivery.cancel()
        w.close(); await w.wait_closed()
async def main():
    core.init_db()
    server=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with server: await server.serve_forever()
if __name__=='__main__': asyncio.run(main())
