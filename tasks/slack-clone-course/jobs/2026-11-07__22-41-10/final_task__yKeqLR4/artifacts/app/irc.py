import socket, threading, sqlite3, json, time, re
from datetime import datetime, timezone
DB='/app/data/huddle.sqlite'
def conn():
 c=sqlite3.connect(DB,check_same_thread=False,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=30000');return c
clients=[]; lock=threading.RLock()
def send(x,s):
 try:x.sendall((s+'\r\n').encode())
 except: pass
def user_for(t):
 c=conn();return c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone()
def worker(sock):
 nick=None; usr=None; token=None; registered=False; joined={}
 buf=b''
 send(sock,':huddle NOTICE * :Welcome')
 try:
  while True:
   data=sock.recv(4096)
   if not data:break
   buf+=data
   while b'\n' in buf:
    line,buf=buf.split(b'\n',1); line=line.decode(errors='ignore').strip('\r ')
    if not line:continue
    a=line.split(' ',2); cmd=a[0].upper()
    if cmd=='PASS' and len(a)>1:
     token=a[1];usr=user_for(token)
     if not usr: send(sock,f':huddle 464 * :Password incorrect')
    elif cmd=='NICK' and len(a)>1:
     new=a[1]
     with lock:
      if any(x[0]==new for x in clients):send(sock,f':huddle 433 * {new} :Nickname is already in use');continue
     nick=new
    elif cmd=='USER' and len(a)>1:
     if not nick:nick=a[1]
     if usr and not registered:
      registered=True
      for n,t in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created now'),('004','huddle huddle 1'),('005','CHANTYPES=#')]:
       send(sock,f':huddle {n} {nick} :{t}')
      send(sock,f':huddle 422 {nick} :MOTD File is missing')
      with lock:clients.append([nick,sock,usr,joined]); 
    elif cmd=='PING':send(sock,':huddle PONG '+(a[1] if len(a)>1 else 'huddle'))
    elif cmd=='PONG':pass
    elif cmd=='QUIT':
     try:sock.shutdown(socket.SHUT_RDWR)
     except:pass
     break
    elif not registered: 
     if cmd in ('NICK','USER','PASS'):continue
     send(sock,f':huddle 451 * :You have not registered')
    elif cmd=='JOIN' and len(a)>1:
     
     name=a[1];c=conn();key=name.lstrip('#');
     z=c.execute('SELECT c.*,w.slug FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(key.split('/',1)[0],key.split('/',1)[1] if '/' in key else '')).fetchone()
     if not z:send(sock,f':huddle 403 {nick} {name} :No such channel');continue
     joined[z['id']]=z['slug']+'/'+z['name']
     with lock:
      for client in clients:
       if client[1] is sock: client[3]=joined
     send(sock,f':{nick}!{usr["username"]}@localhost JOIN {name}')
     members=[x['username'] for x in c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(z['id'],))]
     send(sock,f':huddle 353 {nick} = {name} :{" ".join(members)}');send(sock,f':huddle 366 {nick} {name} :End of /NAMES list.')
    elif cmd=='NAMES' and len(a)>1:send(sock,f':huddle 366 {nick} {a[1]} :End of /NAMES list.')
    elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):send(sock,f':huddle 200 {nick} {cmd} :OK')
    elif cmd=='PRIVMSG' and len(a)>2:
     target=a[1];text=a[2].lstrip(':'); key=target.lstrip('#')
     c=conn();z=c.execute('SELECT c.* FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug||"/"||c.name=?',(key,)).fetchone()
     if z:
      cur=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(z['id'],usr['id'],text,datetime.now(timezone.utc).isoformat()));mid=cur.lastrowid
      seq=c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE channel_id=?',(z['id'],)).fetchone()[0]
      c.execute('INSERT INTO events(channel_id,seq,kind,message_id,data) VALUES(?,?,?,?,?)',(z['id'],seq,'message.created',mid,json.dumps({})));c.commit()
    else:send(sock,f':huddle 421 {nick} {cmd} :Unknown command')
 except:pass
 finally:
  with lock:
   clients[:]=[x for x in clients if x[1] is not sock]
  try:sock.close()
  except:pass
def fanout():
 seen=0
 while True:
  try:
   c=conn()
   for ev in c.execute('SELECT o.*,ch.name,w.slug,u.username FROM irc_outbox o JOIN channels ch ON ch.id=o.channel_id JOIN workspaces w ON w.id=ch.workspace_id JOIN users u ON u.id=o.author_id WHERE o.id>? ORDER BY o.id',(seen,)).fetchall():
    seen=ev['id']; target='#'+ev['slug']+'/'+ev['name']
    with lock:
     for client in list(clients):
      n,sc,u,j=client
      if ev['channel_id'] in j and u['id']!=ev['author_id']:
       send(sc,f':{ev["username"]}!{u["username"]}@localhost PRIVMSG {target} :{ev["body"]}')
  except Exception: pass
  time.sleep(.05)
def main():
 threading.Thread(target=fanout,daemon=True).start()
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
 try:s.bind(('0.0.0.0',6667))
 except OSError:return
 s.listen()
 while True:
  x,_=s.accept();threading.Thread(target=worker,args=(x,),daemon=True).start()
if __name__=='__main__':main()
