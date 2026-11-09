import asyncio, core
NICKS=set(); CLIENTS=[]
async def handle(reader,writer):
    token=None; user=None; nick=None; registered=False; user_seen=False; joined={}; sent={}
    CLIENTS.append((writer,joined,sent))
    async def send(line):
        try: writer.write((line+'\r\n').encode()); await writer.drain()
        except: pass
    async def deliver():
        while not writer.is_closing():
            for cid,name in list(joined.items()):
                c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,sent.get(cid,0))).fetchall(); sent[cid]=sent.get(cid,0)
                c.close()
                for row in rows:
                    import json
                    z=json.loads(row['payload']); sent[cid]=row['event_id']
                    if z.get('type') not in ('message.created','message.reply'): continue
                    m=z.get('message') or {}
                    au=core.user_for_token('') if False else None
                    cc=core.connect(); ar=cc.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone();cc.close()
                    an=ar['username'] if ar else 'user'
                    if an==nick: continue
                    await send(f':{an}!{an}@huddle PRIVMSG {name} :{m.get("body","")}')
            await asyncio.sleep(.05)
    task=asyncio.create_task(deliver())
    try:
      while not reader.at_eof():
        raw=await reader.readline()
        if not raw:break
        line=raw.decode(errors='ignore').strip()
        if not line:continue
        parts=line.split(' ',2);cmd=parts[0].upper()
        if cmd=='PASS':
            token=parts[1] if len(parts)>1 else '';user=core.user_for_token(token)
            if not user:await send(':huddle 464 * :Password incorrect');token=None
        elif cmd=='NICK':
            requested=parts[1] if len(parts)>1 else ''
            if requested in NICKS:await send(f':huddle 433 * {requested} :Nickname is already in use');continue
            if nick:NICKS.discard(nick)
            nick=requested;NICKS.add(nick)
            if token and user_seen and not registered:
                registered=True
                for n in ('001','002','003','004','005'):await send(f':huddle {n} {nick} :Huddle')
                await send(f':huddle 422 {nick} :MOTD File is missing')
        elif cmd=='USER':
            user_seen=True
            if token and nick and not registered:
                registered=True
                for n in ('001','002','003','004','005'):await send(f':huddle {n} {nick} :Huddle')
                await send(f':huddle 422 {nick} :MOTD File is missing')
        elif cmd=='PING':await send(':huddle PONG '+(parts[1] if len(parts)>1 else 'huddle'))
        elif cmd=='QUIT':break
        elif not registered:continue
        elif cmd=='JOIN':
            name=parts[1] if len(parts)>1 else ''; ch=None
            if name.startswith('#') and '/' in name:
                slug,cn=name[1:].split('/',1);w=core.get_workspace(slug)
                if w:
                    c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cn)).fetchone();c.close()
            if not ch:await send(f':huddle 403 {nick} {name} :No such channel');continue
            joined[ch['id']]=name;sent[ch['id']]=ch['last_event_id'];await send(f':{nick}!{nick}@huddle JOIN {name}');await send(f':huddle 353 {nick} = {name} :{nick}');await send(f':huddle 366 {nick} {name} :End of /NAMES list.')
        elif cmd=='PRIVMSG':
            name=parts[1] if len(parts)>1 else '';body=parts[2][1:] if len(parts)>2 and parts[2].startswith(':') else (parts[2] if len(parts)>2 else '')
            cid=next((i for i,n in joined.items() if n==name),None)
            if cid is not None:
                r,st=core.create_message(user,cid,body)
                if st==201: sent[cid]=r and sent.get(cid,0)
        elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):continue
        else:await send(f':huddle 421 {nick} {parts[0]} :Unknown command')
    finally:
      task.cancel();
      try: CLIENTS.remove((writer,joined,sent))
      except ValueError: pass
      if nick:NICKS.discard(nick)
      writer.close()
      try:await writer.wait_closed()
      except:pass
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s:await s.serve_forever()
if __name__=='__main__':asyncio.run(main())
