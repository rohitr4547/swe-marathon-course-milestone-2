import socket,threading,sqlite3
DB='/app/huddle.db'
nicks=set()
nicks_lock=threading.Lock()
def send(c,s): c.sendall((s+'\r\n').encode())
def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def client(c,a):
 c.settimeout(0.25)
 nick=None; token=None; user=None; joined={}; seen={}
 try:
  buf=b''
  while 1:
   for ch,last in list(seen.items()):
    x=ch[1:].split('/',1)
    if len(x)==2:
     for q in db().execute('SELECT m.*,u.username FROM messages m JOIN channels c ON c.id=m.channel_id JOIN workspaces w ON w.id=c.workspace_id JOIN users u ON u.id=m.author_id WHERE w.slug=? AND c.name=? AND m.event_id>? ORDER BY m.event_id',(x[0],x[1],last)):
      if q['author_id']!=user: send(c,':'+q['username']+'!user@localhost PRIVMSG '+ch+' :'+q['body'])
      seen[ch]=q['event_id']
   try: z=c.recv(4096)
   except socket.timeout: continue
   if not z: break
   buf+=z
   while b'\r\n' in buf:
    raw,buf=buf.split(b'\r\n',1); line=raw.decode(errors='ignore'); p=line.split()
    if not p: continue
    cmd=p[0].upper()
    if cmd=='PASS': token=p[1] if len(p)>1 else ''
    elif cmd=='NICK':
     requested=p[1] if len(p)>1 else 'guest'
     with nicks_lock:
      collision=requested.lower() in nicks and requested.lower() != (nick or '').lower()
      if not collision:
       if nick: nicks.discard(nick.lower())
       nicks.add(requested.lower())
       nick=requested
     if collision: send(c,':irc 433 * '+requested+' :Nickname in use')
     elif user is not None:
      for n,m in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','irc huddle 1 1'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]: send(c,':irc '+n+' '+nick+' :'+m)
    elif cmd=='USER':
     if not token or not db().execute('SELECT user_id FROM tokens WHERE token=?',(token,)).fetchone():
      send(c,':irc 464 * :Password incorrect'); continue
     user=db().execute('SELECT user_id FROM tokens WHERE token=?',(token,)).fetchone()['user_id']
     if nick:
      for n,m in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','irc huddle 1 1'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]: send(c,':irc '+n+' '+nick+' :'+m)
    elif cmd=='PING': send(c,':irc PONG '+(' '.join(p[1:]) if len(p)>1 else 'irc'))
    elif cmd=='QUIT':
     try: c.shutdown(socket.SHUT_RDWR)
     except Exception: pass
     break
    elif cmd in ('JOIN','NAMES'):
     ch=p[1] if len(p)>1 else ''
     x=ch[1:].split('/',1) if ch.startswith('#') else []
     r=db().execute('SELECT w.slug,c.name,c.topic FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(x[0],x[1] if len(x)>1 else '')).fetchone() if len(x)==2 else None
     if not r: send(c,':irc 403 '+(nick or '*')+' '+ch+' :No such channel'); continue
     joined[ch]=1
     seen[ch]=db().execute('SELECT coalesce(max(event_id),0) n FROM messages m JOIN channels c ON c.id=m.channel_id JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(x[0],x[1])).fetchone()['n']
     send(c,':'+(nick or 'guest')+'!user@localhost JOIN '+ch)
     send(c,':irc 353 '+(nick or '*')+' = '+ch+' :'+(nick or 'guest')); send(c,':irc 366 '+(nick or '*')+' '+ch+' :End of NAMES')
     send(c,':irc 331 '+(nick or '*')+' '+ch+' :No topic')
    elif cmd=='PRIVMSG' and len(p)>=3:
     ch=p[1]; body=line.split(' :',1)[1] if ' :' in line else ' '.join(p[2:])
     if ch in joined:
      x=ch[1:].split('/',1); r=db().execute('SELECT c.id FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(x[0],x[1])).fetchone()
      if r:
       q=db(); ev=q.execute('SELECT coalesce(max(event_id),0)+1 n FROM messages WHERE channel_id=?',(r['id'],)).fetchone()['n']; q.execute('INSERT INTO messages(channel_id,author_id,body,created_at,event_id) VALUES(?,?,?,?,?)',(r['id'],user,body,__import__('datetime').datetime.utcnow().isoformat()+'Z',ev));q.commit()
    elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
     if cmd=='NAMES' and len(p)>1:
      send(c,':irc 353 '+(nick or '*')+' = '+p[1]+' :'+(nick or 'guest'))
      send(c,':irc 366 '+(nick or '*')+' '+p[1]+' :End of NAMES')
     elif cmd=='WHO':
      send(c,':irc 315 '+(nick or '*')+' '+(p[1] if len(p)>1 else '*')+' :End of WHO')
     elif cmd=='LIST':
      send(c,':irc 323 '+(nick or '*')+' :End of LIST')
     elif cmd=='TOPIC':
      send(c,':irc 331 '+(nick or '*')+' '+(p[1] if len(p)>1 else '*')+' :No topic')
    else: send(c,':irc 421 '+(nick or '*')+' '+cmd+' :Unknown command')
 finally:
  if nick:
   with nicks_lock: nicks.discard(nick.lower())
  c.close()
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('0.0.0.0',6667)); s.listen()
while 1:
 c,a=s.accept(); threading.Thread(target=client,args=(c,a),daemon=True).start()
