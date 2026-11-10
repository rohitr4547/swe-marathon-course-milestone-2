import asyncio,core
clients=set()
def line(w,s): w.write((s+'\r\n').encode())
async def handle(r,w):
 clients.add(w); user=None; nick=None; user_sent=False; registered=False; joined=set(); last={}
 async def deliver():
  while not w.is_closing():
   await asyncio.sleep(.05)
   for cid,seen in list(last.items()):
    c=core.conn(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,seen)).fetchall()
    for row in rows:
     f=__import__('json').loads(row['payload']); last[cid]=f['event_id']
     if f.get('type')=='message.created':
      m=f.get('message') or {}; au=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone()
      ch=c.execute('SELECT ch.name,w.slug FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE ch.id=?',(cid,)).fetchone()
      if au and ch and au[0] != getattr(w,'_nick',None):
       line(w,f':{au[0]}!{au[0]}@localhost PRIVMSG #{ch[1]}/{ch[0]} :{m.get("body","")}')
       await w.drain()
    c.close()
 try:
  task=asyncio.create_task(deliver())
  while True:
   raw=await r.readline()
   if not raw: break
   p=raw.decode(errors='ignore').strip().split(' ',2); cmd=p[0].upper(); args=p[1:]
   if cmd=='PASS':
    user=core.token_user(args[0] if args else '')
    if not user: line(w,':huddle 464 * :Password incorrect'); continue
   elif cmd=='NICK':
    n=args[0] if args else ''
    if any(getattr(x,'_nick',None)==n for x in clients if x is not w): line(w,':huddle 433 * '+n+' :Nickname in use')
    else:
     nick=n; w._nick=n
     if user and user_sent and not registered:
      for code in ('001','002','003','004','005','422'): line(w,f':huddle {code} {nick} :huddle')
      registered=True
   elif cmd=='USER':
    user_sent=True
    if user and nick and not registered:
     for n in ('001','002','003','004','005','422'): line(w,f':huddle {n} {nick} :huddle')
     registered=True
   elif cmd=='PING': line(w,':huddle PONG huddle '+(args[-1].lstrip(':') if args else ''))
   elif cmd=='QUIT': break
   elif cmd in ('PONG','WHO','LIST','TOPIC','MODE','NAMES'): pass
   elif cmd=='JOIN' and user:
    name=args[0] if args else ''; c=core.conn(); ch=c.execute('SELECT ch.*,w.slug FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE \"#\"||w.slug||\"/\"||ch.name=?',(name,)).fetchone();c.close()
    if not ch: line(w,f':huddle 403 {nick} {name} :No such channel'); continue
    joined.add(ch['id']); hc=core.conn(); head=hc.execute('SELECT last_event_id FROM channels WHERE id=?',(ch['id'],)).fetchone(); hc.close(); last[ch['id']]=head[0] if head else 0; un=user['username']; line(w,f':{nick}!{un}@localhost JOIN {name}'); line(w,f':huddle 353 {nick} = {name} :{nick}'); line(w,f':huddle 366 {nick} {name} :End of NAMES')
   elif cmd=='PRIVMSG' and user and len(p)>=3:
    name=args[0]; text=p[2].lstrip(':');c=core.conn();ch=c.execute('SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE \"#\"||w.slug||\"/\"||ch.name=?',(name,)).fetchone();c.close()
    if ch: core.make_message(user['id'],ch['id'],text)
   else:
    who=nick or '*'; line(w,f':huddle 421 {who} {cmd} :Unknown command')
   await asyncio.sleep(0)
 finally:
  if 'task' in locals(): task.cancel()
  clients.discard(w);w.close();await w.wait_closed()
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s: await s.serve_forever()
if __name__=='__main__': asyncio.run(main())
