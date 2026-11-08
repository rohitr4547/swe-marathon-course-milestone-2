import asyncio,core
clients=set()
async def handle(reader,writer):
    clients.add(writer); nick=None; user=None; token=None; registered=False
    try:
      while True:
        line=await reader.readline()
        if not line:break
        s=line.decode(errors='ignore').strip(); parts=s.split(' ',2); cmd=parts[0].upper() if parts else ''
        if cmd=='PASS' and len(parts)>1:token=parts[1]
        elif cmd=='NICK' and len(parts)>1:
          nick=parts[1]
          if registered: await send(writer,':huddle 433 * '+nick+' :Nickname in use')
        elif cmd=='USER' and len(parts)>1:
          user=core.user_for_token(token)
          if not user: await send(writer,':huddle 464 * :Password incorrect');break
          registered=True
          for n,text in [('001','Welcome'),('002','Your host'),('003','Created'),('004','huddle'),('005','CHANTYPES=#')]:
            await send(writer,f':huddle {n} {nick} :{text}')
          await send(writer,f':huddle 422 {nick} :MOTD File is missing')
        elif cmd=='PING':await send(writer,':huddle PONG '+(parts[1] if len(parts)>1 else ''))
        elif cmd=='PONG':pass
        elif cmd=='QUIT':break
        elif cmd=='JOIN' and len(parts)>1:
          await send(writer,f':{nick}!{user["username"] if user else "user"}@localhost JOIN {parts[1]}')
          await send(writer,f':huddle 353 {nick} = {parts[1]} :{nick}')
          await send(writer,f':huddle 366 {nick} {parts[1]} :End of NAMES list')
        elif cmd=='PRIVMSG' and len(parts)>2 and user:
          target=parts[1];body=parts[2][1:] if parts[2].startswith(':') else parts[2]
          if target.startswith('#') and '/' in target:
            slug,name=target[1:].split('/',1);w=core.get_workspace(slug)
            if w:
              c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone();c.close()
              if ch:
                try:core.create_message(user,ch['id'],body)
                except:pass
        elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'):pass
        else:
          if registered:await send(writer,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
    finally:clients.discard(writer);writer.close();await writer.wait_closed()
async def send(w,s):w.write((s+'\r\n').encode());await w.drain()
async def main():
    core.init_db(); srv=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with srv:
        await srv.serve_forever()
if __name__=='__main__':asyncio.run(main())
