import asyncio, core, re
clients={}
connections=[]
def send(w,line): w.write((line+'\r\n').encode())
def channel_for(name):
 if not name.startswith('#') or '/' not in name[1:]: return None
 slug,cname=name[1:].split('/',1); ws=core.get_workspace(slug)
 if not ws:return None
 c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],cname)).fetchone();c.close()
 return ch
async def deliver(conn):
 last={}
 while not conn['writer'].is_closing():
  for cid in list(conn['joined']):
   for e in core.events_after(cid,last.get(cid,0)):
    last[cid]=e['event_id']
    if e.get('type') not in ('message.created','message.reply'): continue
    m=e.get('message') or {}; au=core.user_for_id(m.get('author_id'))
    if m.get('author_id')==conn.get('user_id'): continue
    c=core.connect(); row=c.execute('SELECT w.slug,ch.name FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE ch.id=?',(cid,)).fetchone();c.close()
    if row and au: send(conn['writer'],f":{au['username']}!{au['username']}@huddle PRIVMSG #{row['slug']}/{row['name']} :{m.get('body','')}")
  await asyncio.sleep(.05)

async def handle(reader,writer):
 nick=None; user=None; token=None; registered=False; joined=[]; conn={'writer':writer,'joined':joined}
 connections.append(conn)
 delivery=None
 try:
  while True:
   raw=await reader.readline()
   if not raw:break
   line=raw.decode(errors='replace').rstrip('\r\n')
   if not line:continue
   bits=line.split(' ',1); cmd=bits[0].upper(); arg=bits[1] if len(bits)>1 else ''
   if cmd=='PASS': token=arg.lstrip(':').strip()
   elif cmd=='NICK':
    n=arg.split()[0] if arg else ''
    if n in clients and clients[n] is not writer:
     send(writer,f':huddle 433 * {n} :Nickname is already in use');continue
    nick=n
   elif cmd=='USER': user=arg.split()[0] if arg else ''
   if cmd in ('NICK','USER') and nick and user and not registered:
    if not token or not core.user_for_token(token):
     send(writer,f':huddle 464 {nick} :Password incorrect'); break
    registered=True; clients[nick]=writer; conn['user_id']=core.user_for_token(token)['id']; delivery=asyncio.create_task(deliver(conn))
    for code,text in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle 1.0'),('005','CHANTYPES=#')]:
     send(writer,f':huddle {code} {nick} :{text}')
    send(writer,f':huddle 422 {nick} :MOTD File is missing')
   elif cmd=='QUIT':break
   elif cmd=='PING':
    val=arg[1:] if arg.startswith(':') else arg;send(writer,f':huddle PONG huddle :{val}')
   elif cmd=='PONG':pass
   elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'):pass
   elif cmd=='JOIN':
    if not registered:
     continue
    name=arg.split()[0] if arg else ''; ch=channel_for(name)
    if not ch:
     send(writer,f':huddle 403 {nick} {name} :No such channel');continue
    u=core.user_for_token(token)
    with core.tx() as c: core.add_channel_member(c,ch['id'],u['id'])
    joined.append(ch['id']); conn['joined']=joined; send(writer,f':{nick}!{user}@huddle JOIN {name}')
    c=core.connect(); names=c.execute('SELECT u.username FROM users u JOIN members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    send(writer,f':huddle 353 {nick} = {name} :{" ".join(x[0] for x in names)}')
    send(writer,f':huddle 366 {nick} {name} :End of /NAMES list')
   elif cmd=='PRIVMSG':
    if not registered: continue
    bits=arg.split(' ',1); target=bits[0] if bits else ''; body=bits[1] if len(bits)>1 else ''
    if body.startswith(':'):body=body[1:]
    ch=channel_for(target)
    if ch and ch['id'] in joined and body:
     core.create_message(core.user_for_token(token),ch['id'],body)
   elif cmd=='NOTICE': pass
   else:
    send(writer,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
  if nick and clients.get(nick) is writer:del clients[nick]
 finally:
  if delivery: delivery.cancel()
  if conn in connections: connections.remove(conn)
  writer.close()
  try:await writer.wait_closed()
  except:pass
async def main():
 core.init_db()
 server=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with server:await server.serve_forever()
if __name__=='__main__':asyncio.run(main())
