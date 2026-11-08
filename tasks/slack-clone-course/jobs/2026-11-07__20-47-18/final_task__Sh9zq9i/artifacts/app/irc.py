import asyncio,core,re
clients=set()
class Client:
 def __init__(self,r,w): self.r=r;self.w=w;self.token=None;self.user=None;self.nick=None;self.registered=False;self.channels=set();self.seen={}
 def send(self,x): self.w.write((x+'\r\n').encode())
 def prefix(self): return (self.nick or '*')+'!user@localhost'
async def handle(r,w):
 c=Client(r,w);clients.add(c)
 try:
  while True:
   line=await r.readline()
   if not line:break
   x=line.decode(errors='ignore').strip();p=x.split(' ',2);cmd=p[0].upper();arg=p[1] if len(p)>1 else '';rest=p[2] if len(p)>2 else ''
   if cmd=='PASS':
    c.token=arg;c.user=core.user_for_token(arg)
    if not c.user:c.send(':huddle 464 * :Password incorrect')
   elif cmd=='NICK':
    if any(z.nick==arg for z in clients if z is not c):c.send(':huddle 433 * '+arg+' :Nickname is already in use');continue
    c.nick=arg
    if c.user and not c.registered and getattr(c,'user_seen',False):
     c.registered=True
     for n in ('001','002','003','004','005'):c.send(':huddle '+n+' '+c.nick+' :Welcome')
     c.send(':huddle 422 '+c.nick+' :MOTD File is missing')
    c.user_seen=True
   elif cmd=='USER':
    if not c.user:continue
    c.user_seen=True
    if c.nick and not c.registered:
     c.registered=True
     for n in ('001','002','003','004','005'):c.send(':huddle '+n+' '+c.nick+' :Welcome')
     c.send(':huddle 422 '+c.nick+' :MOTD File is missing')
   elif not c.registered: continue
   elif cmd=='PING':c.send(':huddle PONG huddle '+(rest.lstrip(':') if rest else arg.lstrip(':')))
   elif cmd=='PONG':pass
   elif cmd=='JOIN':
    name=arg.lstrip('#');a=name.split('/')
    if len(a)!=2 or '-' in a[0]:c.send(':huddle 403 '+c.nick+' '+arg+' :No such channel');continue
    wspace=core.get_workspace(a[0]);ch=None
    if wspace:
     db=core.connect();ch=db.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(wspace['id'],a[1])).fetchone();db.close()
    if not ch:c.send(':huddle 403 '+c.nick+' '+arg+' :No such channel');continue
    with core.tx() as db:core.add_channel_member(db,ch['id'],c.user['id'])
    c.channels.add(ch['id']);c.send(':'+c.prefix()+' JOIN '+arg);c.send(':huddle 353 '+c.nick+' = '+arg+' :'+c.nick);c.send(':huddle 366 '+c.nick+' '+arg+' :End of NAMES')
   elif cmd=='PRIVMSG':
    body=rest.lstrip(':');a=arg.lstrip('#').split('/')
    if len(a)==2:
     wspace=core.get_workspace(a[0]);db=core.connect();ch=db.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(wspace['id'],a[1])).fetchone() if wspace else None;db.close()
     if ch and ch['id'] in c.channels:
      try:core.create_message(c.user,ch['id'],body)
      except:pass
   elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'):c.send(':huddle 366 '+c.nick+' '+arg+' :End')
   elif cmd=='QUIT':break
   else:c.send(':huddle 421 '+c.nick+' '+cmd+' :Unknown command')
 finally:
  clients.discard(c);w.close();await w.wait_closed()
async def deliver():
 while True:
  for c in list(clients):
   for ch in list(c.channels):
    for e in core.events_after(ch,c.seen.get(ch,0)):
     c.seen[ch]=e['event_id']
     if e.get('type')=='message.created':
      m=e.get('message') or {}; db=core.connect();row=db.execute('SELECT w.slug,c.name FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE c.id=?',(ch,)).fetchone();db.close()
      if row and m.get('author_id')!=c.user['id']: c.send(':'+str((m.get('author') or {}).get('username',c.nick))+'!user@localhost PRIVMSG #'+row['slug']+'/'+row['name']+' :'+m.get('body',''))
  await asyncio.sleep(.05)
async def main():
 core.init_db();s=await asyncio.start_server(handle,'0.0.0.0',6667);asyncio.create_task(deliver())
 async with s:await s.serve_forever()
if __name__=='__main__':asyncio.run(main())
