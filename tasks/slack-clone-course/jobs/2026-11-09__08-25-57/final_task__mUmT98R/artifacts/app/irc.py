import asyncio, core, re
NICKS=set()
def line(s): return (s+'\r\n').encode()
def channel_by_name(name):
 if not name.startswith('#') or '/' not in name[1:]: return None
 slug,cname=name[1:].split('/',1)
 w=core.get_workspace(slug)
 if not w:return None
 c=core.connect(); r=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone();c.close()
 return r
async def deliver(state,writer):
 while True:
  for cid,(name,last) in list(state['joined'].items()):
   for ev in core.events_after(cid,last):
    state['joined'][cid]=(name,ev['event_id'])
    if ev.get('type') not in ('message.created','message.reply'): continue
    msg=ev.get('message') or {}
    if msg.get('author_id')==state['user']['id']: continue
    c=core.connect(); a=c.execute('SELECT username FROM users WHERE id=?',(msg.get('author_id'),)).fetchone();c.close()
    if a:
     writer.write(line(f":{a['username']}!{a['username']}@huddle PRIVMSG {name} :{msg.get('body','')}")); await writer.drain()
  await asyncio.sleep(.05)
async def handle(reader,writer):
 nick=None; username=None; token=None; user=None; registered=False
 state={'joined':{},'user':None}; delivery_task=None
 async def send(x): writer.write(line(x)); await writer.drain()
 try:
  while True:
   raw=await reader.readline()
   if not raw: break
   text=raw.decode(errors='replace').strip('\r\n')
   if not text: continue
   parts=text.split(' ',2); cmd=parts[0].upper(); rest=parts[1:]
   if cmd=='PASS':
    token=rest[0] if rest else ''
   elif cmd=='NICK':
    new=rest[0] if rest else ''
    if new in NICKS and new!=nick:
     await send(f':huddle 433 * {new} :Nickname is already in use'); continue
    if nick:NICKS.discard(nick)
    nick=new; NICKS.add(nick)
   elif cmd=='USER':
    username=(rest[0] if rest else '') or nick
   elif cmd=='QUIT':
    break
   elif cmd=='PRIVMSG' and registered and len(rest)>=2:
    name=rest[0]; body=rest[1]
    if body.startswith(':'): body=body[1:]
    ch=channel_by_name(name)
    if not ch or ch['id'] not in state['joined']:
     await send(f':huddle 404 {nick} {name} :Cannot send to channel'); continue
    with core.tx() as c:
     cur=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(ch['id'],user['id'],body,core.now_iso()))
     mid=cur.lastrowid; x=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
     core.emit_event(c,ch['id'],'message.created',{'message':core.message_to_json(x)})
   elif cmd=='PING':
    tok=text.split(':',1)[1] if ':' in text else (rest[0] if rest else '')
    await send(f':huddle PONG huddle :{tok}')
   elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
    if cmd=='NAMES' and len(rest):
     ch=channel_by_name(rest[-1])
     if ch:
      c=core.connect(); ns=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
      await send(f':huddle 353 {nick} = {rest[-1]} :{" ".join(x[0] for x in ns)}')
      await send(f':huddle 366 {nick} {rest[-1]} :End of /NAMES list')
   elif cmd=='JOIN' and rest:
    if not registered:
     continue
    name=rest[0]; ch=channel_by_name(name)
    if not ch:
     await send(f':huddle 403 {nick} {name} :No such channel'); continue
    with core.tx() as c:
     core.add_workspace_member(c,ch['workspace_id'],user['id']); core.add_channel_member(c,ch['id'],user['id'])
    state['joined'][ch['id']]=(name,core.head_event_id(ch['id']))
    await send(f':{nick}!{username}@huddle JOIN {name}')
    await send(f':huddle 353 {nick} = {name} :{username}')
    await send(f':huddle 366 {nick} {name} :End of /NAMES list')
   else:
    await send(f':huddle 421 {nick or "*"} {cmd} :Unknown command')
   if not registered and nick and username is not None:
    user=core.user_for_token(token or '')
    if not user:
     await send(f':huddle 464 {nick} :Password incorrect'); break
    registered=True
    state['user']=user
    delivery_task=asyncio.create_task(deliver(state,writer))
    for code,msg in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle'),('005','CHANTYPES=#')]:
     await send(f':huddle {code} {nick} :{msg}')
    await send(f':huddle 422 {nick} :MOTD File is missing')
 finally:
  if delivery_task: delivery_task.cancel()
  if nick:NICKS.discard(nick)
  writer.close()
  try: await writer.wait_closed()
  except: pass
async def main():
 server=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with server: await server.serve_forever()
if __name__=='__main__': asyncio.run(main())
