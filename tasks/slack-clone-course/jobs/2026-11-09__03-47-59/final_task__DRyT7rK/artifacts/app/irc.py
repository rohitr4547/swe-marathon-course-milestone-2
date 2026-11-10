import asyncio,re,json,core
clients=[]
def line(w,s): w.write((s+'\r\n').encode())
def chan_lookup(name):
 if not name.startswith('#') or '/' not in name[1:]: return None
 slug,n=name[1:].split('/',1); w=core.get_workspace(slug)
 if not w:return None
 c=core.connect(); x=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],n)).fetchone();c.close();return x
def chan_name(ch):
 c=core.connect();w=c.execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
 return '#'+w['slug']+'/'+ch['name']
async def deliver(cl):
 while cl['alive']:
  for cid in list(cl['joined']):
   c=core.connect(); rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,cl['last'].get(cid,0))).fetchall();c.close()
   for e in rows:
    z=json.loads(e['payload']);cl['last'][cid]=e['event_id']
    if z.get('type') not in ('message.created','message.reply'):continue
    m=z.get('message') or {}; author=core.user_for_token(cl['token'])
    if m.get('author_id')==cl['user']['id'] and cid in cl['sent']: 
     cl['sent'].discard(cid);continue
    ch=core.get_channel(cid); line(cl['writer'],':%s!%s@huddle PRIVMSG %s :%s'%(m.get('author',{}).get('username','user'),m.get('author',{}).get('username','user'),chan_name(ch),m.get('body','')))
  await asyncio.sleep(.05)
async def handle(r,w):
 cl={'writer':w,'alive':True,'token':None,'user':None,'nick':None,'registered':False,'joined':set(),'last':{},'sent':set()}
 clients.append(cl); task=None
 try:
  while True:
   raw=await r.readline()
   if not raw:break
   text=raw.decode(errors='ignore').strip('\r\n')
   if not text:continue
   parts=text.split(' ',1);cmd=parts[0].upper();rest=parts[1] if len(parts)>1 else ''
   args=rest.split()
   if cmd=='PASS':cl['token']=args[0] if args else ''
   elif cmd=='NICK':
    nick=rest.split()[0] if rest else ''
    if any(x.get('nick')==nick and x is not cl for x in clients): line(w,':huddle 433 * %s :Nickname is already in use'%nick);continue
    cl['nick']=nick
   elif cmd=='USER':
    cl['hasuser']=True
   elif cmd=='QUIT':break
   if cl.get('nick') and cl.get('hasuser') and not cl['registered']:
    cl['user']=core.user_for_token(cl['token'] or '')
    if not cl['user']:line(w,':huddle 464 %s :Password incorrect'% (cl['nick'] or '*'));break
    cl['registered']=True
    for n,t in [('001','Welcome to huddle'),('002','Your host is huddle'),('003','Server created'),('004','huddle 1.0'),('005','CHANTYPES=#')]:
     line(w,':huddle %s %s :%s'%(n,cl['nick'],t))
    line(w,':huddle 422 %s :MOTD File is missing'%cl['nick']);task=asyncio.create_task(deliver(cl))
    continue
   if not cl['registered']:continue
   if cmd=='PING':line(w,':huddle PONG huddle :'+rest.lstrip(':'))
   elif cmd=='PONG':pass
   elif cmd=='JOIN':
    name=args[0] if args else '';ch=chan_lookup(name)
    if not ch or ch['id'] in cl['joined'] and False:line(w,':huddle 403 %s %s :No such channel'%(cl['nick'],name));continue
    try:
     with core.tx() as c: core.add_channel_member(c,ch['id'],cl['user']['id'])
     cl['joined'].add(ch['id']);cl['last'][ch['id']]=ch['last_event_id']
     line(w,':%s!%s@huddle JOIN %s'%(cl['nick'],cl['nick'],name))
     c=core.connect(); ns=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
     line(w,':huddle 353 %s = %s :%s'%(cl['nick'],name,' '.join(x['username'] for x in ns)));line(w,':huddle 366 %s %s :End of /NAMES list'%(cl['nick'],name))
    except:line(w,':huddle 403 %s %s :No such channel'%(cl['nick'],name))
   elif cmd=='PRIVMSG':
    if not args:continue
    name=args[0];body=rest.split(' :',1)[1] if ' :' in rest else ''
    ch=chan_lookup(name)
    if not ch or ch['id'] not in cl['joined']:continue
    core.create_message(cl['user'],ch['id'],body);cl['sent'].add(ch['id'])
   elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'):pass
   else:line(w,':huddle 421 %s %s :Unknown command'%(cl['nick'],cmd))
 finally:
  cl['alive']=False
  if task:task.cancel()
  clients.remove(cl);w.close();await w.wait_closed()
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s:await s.serve_forever()
asyncio.run(main())
