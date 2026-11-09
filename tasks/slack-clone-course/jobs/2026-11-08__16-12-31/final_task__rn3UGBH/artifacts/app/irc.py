import socket,threading,sqlite3,json,datetime,os,time
CLIENTS=[]; LOCK=threading.Lock()
DB='/app/data/huddle.db'
def db():
 c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row;return c
def send(c,s): c.sendall((s+'\r\n').encode())
def client(c,a):
 client_state={'sock':c,'uid':None,'joined':{}}
 with LOCK:
  CLIENTS.append(client_state)
 token=None; uid=None; nick=None; joined={}; registered=False
 try:
  buf=b''
  while True:
   x=c.recv(4096)
   if not x: break
   buf+=x
   while b'\n' in buf:
    line,buf=buf.split(b'\n',1); line=line.decode(errors='ignore').strip('\r ')
    if not line: continue
    q=line.split(' ',2); cmd=q[0].upper(); rest=q[1:]
    if cmd=='PASS': token=rest[0] if rest else ''
    elif cmd=='NICK':
     nick=rest[0] if rest else ''
     if db().execute('SELECT 1 FROM users WHERE username=?',(nick,)).fetchone() and not uid: send(c,':huddle 433 * '+nick+' :Nickname in use')
    elif cmd=='USER' and token and nick and not registered:
     r=db().execute('SELECT user_id FROM tokens WHERE token=?',(token,)).fetchone()
     if not r: send(c,':huddle 464 * :Password incorrect'); continue
     uid=r['user_id']; client_state['uid']=uid; registered=True
     for n in ('001','002','003','004','005'): send(c,':huddle '+n+' '+nick+' :Huddle')
     send(c,':huddle 422 '+nick+' :MOTD File is missing')
    elif cmd=='PING': send(c,':huddle PONG '+(rest[-1] if rest else ''))
    elif cmd=='QUIT':
     try: c.shutdown(socket.SHUT_RDWR)
     except Exception: pass
     return
    elif not registered: continue
    elif cmd in ('JOIN','NAMES'):
     name=rest[0] if rest else ''
     if name.startswith('#') and '/' in name:
      slug,chname=name[1:].split('/',1); row=db().execute('SELECT c.id,c.name,w.slug FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,chname)).fetchone()
      if not row: send(c,':huddle 403 '+nick+' '+name+' :No such channel'); continue
      joined[name]=row['id']; client_state['joined']=joined; send(c,':'+nick+'!user@localhost JOIN '+name)
      names=[x['username'] for x in db().execute('SELECT u.username FROM users u JOIN cmembers m ON m.user_id=u.id WHERE m.channel_id=?',(row['id'],))]
      send(c,':huddle 353 '+nick+' = '+name+' :'+(' '.join(names)));send(c,':huddle 366 '+nick+' '+name+' :End of NAMES list')
     else: send(c,':huddle 403 '+nick+' '+name+' :No such channel')
    elif cmd=='PRIVMSG':
     target=rest[0] if rest else ''; body=rest[1].lstrip(':') if len(rest)>1 else ''
     cid=joined.get(target)
     if cid and body:
      d=db(); n=d.execute('SELECT coalesce(max(event_id),0)+1 n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']; now=datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
      d.execute('INSERT INTO messages(channel_id,author_id,body,created_at,event_id) VALUES(?,?,?,?,?)',(cid,uid,body,now,n)); mid=d.execute('SELECT last_insert_rowid()').fetchone()[0]
      row=d.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); d.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(cid,n,'message.created',json.dumps({'message':dict(row)})));d.commit()
    elif cmd in ('PONG','WHO','LIST','TOPIC','MODE'): pass
    else: send(c,':huddle 421 '+nick+' '+cmd+' :Unknown command')
 finally:
  with LOCK:
   if client_state in CLIENTS: CLIENTS.remove(client_state)
  c.close()
def broadcast():
 last={}
 while True:
  try:
   d=db()
   for e in d.execute("SELECT e.*,m.author_id,m.body FROM events e JOIN messages m ON m.id=json_extract(e.payload,'$.message.id') WHERE e.kind='message.created' ORDER BY e.id"):
    if e['id']<=last.get(e['channel_id'],0): continue
    last[e['channel_id']]=e['id']
    payload=json.loads(e['payload']); m=payload.get('message',{}); body=m.get('body',''); author=e['author_id']
    row=d.execute('SELECT u.username,w.slug,c.name FROM users u JOIN messages x ON x.author_id=u.id JOIN channels c ON c.id=x.channel_id JOIN workspaces w ON w.id=c.workspace_id WHERE x.id=?',(m.get('id'),)).fetchone()
    if not row: continue
    chan='#'+row['slug']+'/'+row['name']
    with LOCK:
     for cl in list(CLIENTS):
      if cl['uid']==author or cl['joined'].get(chan)!=e['channel_id']: continue
      try: send(cl['sock'],':%s!%s@localhost PRIVMSG %s :%s'%(row['username'],row['username'],chan,body))
      except Exception: pass
  except Exception: pass
  time.sleep(.02)
def main():
 threading.Thread(target=broadcast,daemon=True).start()
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
 while True:
  c,a=s.accept();threading.Thread(target=client,args=(c,a),daemon=True).start()
main()
