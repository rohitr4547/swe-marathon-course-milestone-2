import socket,threading,sqlite3,os
DB='/app/data/huddle.sqlite'
clients={}; lock=threading.RLock()
def user_for(token):
 try:
  c=sqlite3.connect(DB); r=c.execute('SELECT u.username FROM users u JOIN tokens t ON u.id=t.user_id WHERE t.token=?',(token,)).fetchone(); c.close(); return r[0] if r else None
 except: return None
def send(s,line):
 try:s.sendall((line+'\r\n').encode())
 except:pass
def bridge(sock,nick_ref,joined,last_events):
 while True:
  try:
   if not nick_ref[0]: return
   cdb=sqlite3.connect(DB); cdb.row_factory=sqlite3.Row
   for cn in list(joined):
    try: slug,name=cn[1:].split('/',1)
    except: continue
    rr=cdb.execute('SELECT c.id FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,name)).fetchone()
    if not rr: continue
    cid=rr[0]; last_events.setdefault(cid,0)
    for ev in cdb.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last_events[cid])).fetchall():
     z=__import__('json').loads(ev['payload']); m=z.get('message') or {}; au=(m.get('author') or {}).get('username',nick_ref[0])
     if m.get('author_id') != nick_ref[1]: send(sock,':'+au+'!user@host PRIVMSG '+cn+' :'+m.get('body',''))
     last_events[cid]=ev['event_id']
   cdb.close()
  except: pass
  import time; time.sleep(.05)
def client(sock,addr):
 token=None; nick=None; registered=False; user_seen=False; joined=set(); last_events={}; uid=None; bridge_started=False
 try:
  f=sock.makefile('r',encoding='utf8',errors='ignore')
  for raw in f:
   a=raw.strip().split(' ',2); cmd=a[0].upper() if a else ''
   if cmd=='PASS' and len(a)>1: token=a[1]; continue
   if joined:
    try:
     cdb=sqlite3.connect(DB); cdb.row_factory=sqlite3.Row
     for cn in list(joined):
      slug,name=cn[1:].split('/',1); rr=cdb.execute('SELECT c.id FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,name)).fetchone()
      if rr:
       cid=rr[0]; last_events.setdefault(cid,0)
       for ev in cdb.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last_events[cid])).fetchall():
        z=__import__('json').loads(ev['payload']); body=(z.get('message') or {}).get('body',''); au=(z.get('message') or {}).get('author',{}).get('username',nick); send(sock,':'+au+'!user@host PRIVMSG '+cn+' :'+body); last_events[cid]=ev['event_id']
     cdb.close()
    except: pass
   if cmd=='NICK' and len(a)>1:
    n=a[1]
    with lock:
     if n in clients and clients[n] is not sock:
      send(sock,':irc 433 * '+n+' :Nickname is already in use'); continue
     nick=n; clients[n]=sock
    if token and user_for(token):
     uidrow=sqlite3.connect(DB).execute('SELECT user_id FROM tokens WHERE token=?',(token,)).fetchone(); uid=uidrow[0] if uidrow else None
     if user_seen and not registered:
      registered=True
      threading.Thread(target=bridge,args=(sock,[nick,uid],joined,last_events),daemon=True).start()
      for num,text in [('001','Welcome'),('002','Your host is huddle'),('003','This server'),('004','huddle 1.0 oi'),('005','CHANTYPES=# :are supported')]: send(sock,':irc '+num+' '+nick+' '+text)
      send(sock,':irc 422 '+nick+' :MOTD File is missing')
     continue
    if token is not None: send(sock,':irc 464 * :Password incorrect'); break
   elif cmd=='USER':
    user_seen=True
    if not token or not user_for(token): send(sock,':irc 464 * :Password incorrect'); break
    if nick and not registered:
     registered=True
     threading.Thread(target=bridge,args=(sock,[nick,uid],joined,last_events),daemon=True).start()
     for num,text in [('001','Welcome'),('002','Your host is huddle'),('003','This server'),('004','huddle 1.0 oi'),('005','CHANTYPES=# :are supported')]: send(sock,':irc '+num+' '+nick+' '+text)
     send(sock,':irc 422 '+nick+' :MOTD File is missing')
   elif cmd=='PING': send(sock,'PONG '+(a[1] if len(a)>1 else ''))
   elif cmd=='PONG': pass
   elif cmd=='JOIN' and len(a)>1:
    ch=a[1]
    valid=False
    if ch.startswith('#') and '/' in ch:
     try:
      slug,name=ch[1:].split('/',1); cdb=sqlite3.connect(DB); valid=bool(cdb.execute('SELECT c.id FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,name)).fetchone()); cdb.close()
     except: valid=False
    if not valid: send(sock,':irc 403 '+(nick or '*')+' '+ch+' :No such channel'); continue
    with lock: joined.add(ch)
    send(sock,':'+(nick or '*')+'!user@host JOIN '+ch); send(sock,':irc 353 '+(nick or '*')+' = '+ch+' :'+(nick or '')); send(sock,':irc 366 '+(nick or '*')+' '+ch+' :End of NAMES list')
   elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'): 
    if cmd=='NAMES' and len(a)>1: send(sock,':irc 353 '+(nick or '*')+' = '+a[1]+' :'+(nick or '')); send(sock,':irc 366 '+(nick or '*')+' '+a[1]+' :End of NAMES list')
   elif cmd=='QUIT': break
   elif cmd=='PRIVMSG' and len(a)>2:
    cn=a[1]; body=a[2].lstrip(':')
    try:
     slug,name=cn[1:].split('/',1); cdb=sqlite3.connect(DB); cdb.row_factory=sqlite3.Row
     rr=cdb.execute('SELECT c.id FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,name)).fetchone()
     if rr:
      cid=rr['id']; cdb.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,datetime("now"))',(cid,uid,body)); mid=cdb.execute('SELECT last_insert_rowid()').fetchone()[0]
      cdb.execute('INSERT OR IGNORE INTO seq(channel_id,n) VALUES(?,0)',(cid,)); cdb.execute('UPDATE seq SET n=n+1 WHERE channel_id=?',(cid,)); eid=cdb.execute('SELECT n FROM seq WHERE channel_id=?',(cid,)).fetchone()[0]
      cdb.execute('INSERT INTO events(channel_id,event_id,type,payload) VALUES(?,?,?,?)',(cid,eid,'message.created',__import__('json').dumps({'message':{'id':mid,'channel_id':cid,'author_id':uid,'body':body,'parent_id':None,'reply_count':0,'created_at':'','edited_at':None,'files':[],'reactions':[],'mentions':[],'author':{'username':nick}}}))); cdb.commit(); cdb.close()
    except: pass
   elif cmd: send(sock,':irc 421 '+(nick or '*')+' '+cmd+' :Unknown command')
 except: pass
 finally:
  with lock:
   if nick and clients.get(nick) is sock: del clients[nick]
  sock.close()
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('0.0.0.0',6667)); s.listen(100)
while True:
 c,a=s.accept(); threading.Thread(target=client,args=(c,a),daemon=True).start()
