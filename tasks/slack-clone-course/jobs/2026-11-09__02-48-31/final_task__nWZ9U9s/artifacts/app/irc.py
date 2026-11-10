import asyncio,core
nicks={}; sessions={}
async def deliver(writer, nick, username, owner_id, joined, last):
 while not writer.is_closing():
  for cid in list(joined):
   for ev in core.events_after(cid,last.get(cid,0)):
    last[cid]=ev['event_id']; m=ev.get('message') or {}
    if ev.get('type')=='message.created':
     body=m.get('body','');
     if m.get('author_id')==owner_id: continue
     ac=core.connect(); ar=ac.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone(); ac.close(); prefix=ar['username'] if ar else nick; await writer.drain(); writer.write(f':{prefix}!{prefix}@localhost PRIVMSG {joined[cid]} :{body}\r\n'.encode())
  await asyncio.sleep(.05)
async def handle(reader,writer):
 nick=None; user=None; passed=None; user_seen=False; registered=False; joined={}; last={}; sessions[writer]=(lambda: None)
 async def send(x):
  writer.write((x+'\r\n').encode()); await writer.drain()
 task=None
 try:
  while True:
   raw=await reader.readline()
   if not raw: break
   s=raw.decode(errors='ignore').strip(); p=s.split(' ',2); cmd=p[0].upper()
   if cmd=='PASS' and len(p)>1:
    passed=core.user_for_token(p[1])
    if not passed: await send(':huddle 464 * :Password incorrect')
   elif cmd=='NICK' and len(p)>1:
    new=p[1]
    if new in nicks and nicks[new] is not writer:
     await send(f':huddle 433 {nick or "*"} {new} :Nickname is already in use'); continue
    if nick:nicks.pop(nick,None)
    nick=new;nicks[nick]=writer
    if passed and user_seen and not registered:
     for code,text in [('001','Welcome'),('002','Your host is huddle'),('003','Server created'),('004','huddle 1.0 oi'),('005','CHANTYPES=#'),('422','MOTD File is missing')]:
      await send(f':huddle {code} {nick} :{text}')
     registered=True
   elif cmd=='USER':
    user_seen=True
    if not (nick and passed): continue
    for code,text in [('001','Welcome'),('002','Your host is huddle'),('003','Server created'),('004','huddle 1.0 oi'),('005','CHANTYPES=#'),('422','MOTD File is missing')]:
     await send(f':huddle {code} {nick} :{text}')
    registered=True
   elif cmd=='PING': await send('PONG '+(p[-1] if len(p)>1 else ':huddle'))
   elif cmd=='QUIT': break
   elif cmd=='JOIN' and len(p)>1:
    target=p[1]; parts=target.lstrip('#').split('/')
    w=core.get_workspace(parts[0]) if len(parts)==2 else None
    ch=None
    if w:
     c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],parts[1])).fetchone();c.close()
    if not ch: await send(f':huddle 403 {nick} {target} :No such channel'); continue
    await send(f':{nick}!{passed["username"]}@localhost JOIN {target}')
    await send(f':huddle 353 {nick} = {target} :{nick}')
    await send(f':huddle 366 {nick} {target} :End of NAMES')
    joined[ch['id']]=target; last[ch['id']]=core.head_event_id(ch['id'])
    if task is None: task=asyncio.create_task(deliver(writer,nick,passed['username'],passed['id'],joined,last))
   elif cmd=='PRIVMSG' and len(p)>2 and passed:
    parts=p[1].lstrip('#').split('/')
    if len(parts)==2:
     w=core.get_workspace(parts[0])
     if w:
      c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],parts[1])).fetchone();c.close()
      if ch: core.create_message(passed['id'],ch['id'],p[2].lstrip(':'))
   elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE','USER','CAP'): pass
   elif cmd not in ('PASS',): await send(f':huddle 421 {nick or "*"} {cmd} :Unknown command')
 finally:
  if task: task.cancel()
  if nick and nicks.get(nick) is writer:nicks.pop(nick,None)
  writer.close(); await writer.wait_closed()
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s: await s.serve_forever()
if __name__=='__main__': asyncio.run(main())
