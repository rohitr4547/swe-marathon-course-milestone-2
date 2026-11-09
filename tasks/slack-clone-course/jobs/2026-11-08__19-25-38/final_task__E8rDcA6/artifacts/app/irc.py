import asyncio, core, json, time
def head(cid):
    c=core.connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r[0] if r else 0
clients={}
async def handle(reader,writer):
    token=None; nick=None; user=None; registered=False; joined=set(); last={}
    clients[writer]={'nick':None,'joined':joined,'last':last}
    def send(x): writer.write((x+'\r\n').encode())
    async def deliver():
        while not writer.is_closing():
            for ch in list(joined):
                c=core.connect()
                rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last.get(ch,0))).fetchall()
                rowch=c.execute('SELECT w.slug,ch.name FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE ch.id=?',(ch,)).fetchone();c.close()
                for row in rows:
                    frame=json.loads(row['payload']); last[ch]=row['event_id']
                    if frame.get('type')=='message.created' and rowch:
                        body=(frame.get('message') or {}).get('body','')
                        aid=(frame.get('message') or {}).get('author_id')
                        cc=core.connect(); ar=cc.execute('SELECT username FROM users WHERE id=?',(aid,)).fetchone();cc.close()
                        an=ar['username'] if ar else 'user'
                        if an != nick: send(f':{an}!{an}@localhost PRIVMSG #{rowch["slug"]}/{rowch["name"]} :{body}')
                try: await writer.drain()
                except: return
            await asyncio.sleep(.05)
    task=asyncio.create_task(deliver())
    try:
        while True:
            raw=await reader.readline()
            if not raw:break
            line=raw.decode(errors='ignore').strip()
            if not line:continue
            parts=line.split(' ',1); cmd=parts[0].upper(); arg=parts[1] if len(parts)>1 else ''
            if cmd=='PASS':
                token=arg.strip()
                if not core.user_for_token(token):send(f':huddle 464 {nick or "*"} :Password incorrect')
            elif cmd=='NICK':
                nn=arg.strip().lstrip(':')
                if nn in [x['nick'] for x in clients.values() if x is not clients[writer]]:
                    send(f':huddle 433 {nick or "*"} {nn} :Nickname is already in use'); await writer.drain();continue
                nick=nn;clients[writer]['nick']=nick
                if token and core.user_for_token(token) and user and not registered:
                    registered=True
                    for n in ('001','002','003','004','005'):send(f':huddle {n} {nick} :Welcome')
                    send(f':huddle 422 {nick} :MOTD File is missing')
            elif cmd=='USER':
                user=arg.split()[0] if arg else nick
                if token and core.user_for_token(token) and nick and not registered:
                    registered=True
                    for n in ('001','002','003','004','005'):send(f':huddle {n} {nick} :Welcome')
                    send(f':huddle 422 {nick} :MOTD File is missing')
            elif cmd=='PING':send('PONG '+arg.lstrip(':'))
            elif cmd=='JOIN':
                channel=arg.split()[0] if arg else ''; valid=None
                if channel.startswith('#') and '/' in channel:
                    slug,name=channel[1:].split('/',1);c=core.connect();valid=c.execute('SELECT ch.id FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(slug,name)).fetchone();c.close()
                if not valid:send(f':huddle 403 {nick or "*"} {channel} :No such channel')
                else:
                    ch=valid[0];joined.add(ch);last[ch]=head(ch);send(f':{nick}!{nick}@localhost JOIN :{channel}');send(f':huddle 353 {nick} = {channel} :{nick}');send(f':huddle 366 {nick} {channel} :End of NAMES list')
            elif cmd=='PRIVMSG':
                bits=arg.split(' :',1)
                if len(bits)==2 and bits[0].startswith('#') and '/' in bits[0] and token:
                    slug,name=bits[0][1:].split('/',1);c=core.connect();ch=c.execute('SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(slug,name)).fetchone();ur=core.user_for_token(token);c.close()
                    if ch and ur: core.create_message(ur,ch['id'],bits[1])
            elif cmd=='QUIT':break
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):pass
            else:send(f':huddle 421 {nick or "*"} {cmd} :Unknown command')
            await writer.drain()
    finally:
        task.cancel();clients.pop(writer,None);writer.close()
        try:await writer.wait_closed()
        except:pass
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s:await s.serve_forever()
asyncio.run(main())
