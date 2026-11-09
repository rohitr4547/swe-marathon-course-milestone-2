import asyncio, core, json
clients=set()
def line(s): return (s+'\r\n').encode()

async def deliver(conn):
 last={}
 try:
  while not conn.is_closing():
   for cid,name in list(getattr(conn,'_irc_joined',{}).items()):
    n=last.get(cid,0)
    for e in core.events_after(cid,n):
     last[cid]=e['event_id']
     if e.get('type') not in ('message.created','message.reply'): continue
     msg=e.get('message') or {}
     if msg.get('author_id')==getattr(getattr(conn,'_irc_user',None),'__getitem__',lambda x:None)('id'): continue
     c=core.connect(); a=c.execute('SELECT username FROM users WHERE id=?',(msg.get('author_id'),)).fetchone(); c.close()
     if a:
      conn.write(line(f":{a['username']}!{a['username']}@huddle PRIVMSG {name} :{msg.get('body','')}"))
      await conn.drain()
   await asyncio.sleep(.05)
 except (asyncio.CancelledError,ConnectionResetError,BrokenPipeError):
  pass

def channel_for(name):
 if not name.startswith('#') or '/' not in name[1:]: return None
 slug,cn=name[1:].split('/',1); w=core.get_workspace(slug)
 if not w:return None
 c=core.connect(); r=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cn)).fetchone();c.close();return r
async def handle(reader,writer):
 clients.add(writer); token=None; nick=None; user=None; joined={}; writer._irc_joined=joined; delivery=None
 async def send(s): writer.write(line(s)); await writer.drain()
 try:
  while True:
   raw=await reader.readline()
   if not raw:break
   s=raw.decode(errors='ignore').strip(); parts=s.split(' ',1); cmd=parts[0].upper(); arg=parts[1] if len(parts)>1 else ''
   if cmd=='PASS': token=arg.lstrip(':')
   elif cmd=='NICK':
    nn=arg.split()[0] if arg else ''
    if any(getattr(x,'_irc_nick',None)==nn for x in clients if x is not writer): await send(f':huddle 433 * {nn} :Nickname is already in use')
    else: nick=nn; writer._irc_nick=nn
   elif cmd=='USER': user=arg.split()[0] if arg else ''
   if nick and user and not getattr(writer,'_registered',False):
    u=core.user_for_token(token)
    if not u:
     await send(f':huddle 464 {nick} :Password incorrect');break
    writer._irc_user=u;writer._registered=True; delivery=asyncio.create_task(deliver(writer))
    for code,text in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle'),('005','CHANTYPES=#')]:
     await send(f':huddle {code} {nick} :{text}')
    await send(f':huddle 422 {nick} :MOTD File is missing')
   elif not getattr(writer,'_registered',False): continue
   elif cmd=='PING': await send(':huddle PONG huddle '+arg)
   elif cmd=='JOIN':
    name=arg.split()[0] if arg else ''; ch=channel_for(name)
    if not ch or not core.join_channel if False else False: pass
    if not ch: await send(f':huddle 403 {nick} {name} :No such channel'); continue
    with core.tx() as c: core.add_channel_member(c,ch['id'],writer._irc_user['id'])
    joined[ch['id']]=name; await send(f':{nick}!{user}@huddle JOIN {name}')
    c=core.connect(); ns=c.execute('SELECT u.username FROM users u JOIN members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    await send(f':huddle 353 {nick} = {name} :{" ".join(x[0] for x in ns)}');await send(f':huddle 366 {nick} {name} :End of /NAMES list')
   elif cmd=='PRIVMSG':
    bits=arg.split(' ',1); name=bits[0] if bits else ''; body=bits[1].lstrip(':') if len(bits)>1 else '';ch=channel_for(name)
    if ch and body:
     core.create_message(writer._irc_user,ch['id'],body)
   elif cmd=='QUIT': break
   elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'): continue
   else: await send(f':huddle 421 {nick} {cmd} :Unknown command')
 finally:
  clients.discard(writer)
  if delivery: delivery.cancel()
  writer.close();await writer.wait_closed()
async def main():
 core.init_db()
 srv=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with srv:
  await srv.serve_forever()
if __name__=='__main__':asyncio.run(main())
