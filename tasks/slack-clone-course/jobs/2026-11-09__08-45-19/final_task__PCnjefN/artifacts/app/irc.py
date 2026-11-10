import socket,sqlite3,threading,json,time
DB='/app/data/huddle.db'; clients=[]; guard=threading.Lock()
def db():
 c=sqlite3.connect(DB,check_same_thread=False);c.row_factory=sqlite3.Row
 c.executescript('''CREATE TABLE IF NOT EXISTS irc_nicks(nick TEXT PRIMARY KEY);
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER,is_dm INTEGER,topic TEXT,is_archived INTEGER,event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,event_id INTEGER,kind TEXT,message_id INTEGER,payload TEXT);''');c.commit();return c
def channel(c,name):
 if not name.startswith('#') or '/' not in name[1:]:return None
 slug,ch=name[1:].split('/',1)
 return c.execute('SELECT ch.*,w.slug FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(slug,ch)).fetchone()
def serve(cl):
 nick=None; token=None; uid=None; registered=False; joined=set(); stop=False; last={}
 def send(x):
  try:cl.sendall((x+'\r\n').encode())
  except:pass
 def reg():
  nonlocal registered,uid
  if registered or not nick or not token:return
  c=db();u=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone()
  if not u:return
  if c.execute('SELECT 1 FROM irc_nicks WHERE nick=?',(nick,)).fetchone():send(':huddle 433 * '+nick+' :Nickname is already in use');return
  c.execute('INSERT OR IGNORE INTO irc_nicks VALUES(?)',(nick,));c.commit();uid=u['id'];registered=True
  for n,t in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle'),('005','CHANTYPES=#')]:send(':huddle '+n+' '+nick+' :'+t)
  send(':huddle 422 '+nick+' :MOTD File is missing')
 def poll():
  while not stop:
   try:
    c=db()
    for name in list(joined):
     ch=channel(c,name)
     if not ch:continue
     last.setdefault(ch['id'],0)
     for e in c.execute("SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id",(ch['id'],last[ch['id']])).fetchall():
      last[ch['id']]=e['event_id']
      if e['kind']=='message.created':
       p=json.loads(e['payload'] or '{}');m=p.get('message') or {}
       if m.get('author_id')==uid:continue
       au=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone()
       if au:send(':'+au['username']+'!user@huddle PRIVMSG '+name+' :'+m.get('body',''))
   except:pass
   time.sleep(.05)
 threading.Thread(target=poll,daemon=True).start()
 buf=b''
 try:
  while True:
   x=cl.recv(4096)
   if not x:break
   buf+=x
   while b'\n' in buf:
    raw,buf=buf.split(b'\n',1);line=raw.decode(errors='ignore').strip();parts=line.split();cmd=parts[0].upper() if parts else ''
    if cmd=='PASS':
     token=line[5:].strip();c=db();ok=c.execute('SELECT 1 FROM tokens WHERE token=?',(token,)).fetchone();c.close()
     if not ok:send(':huddle 464 * :Password incorrect')
    elif cmd=='NICK' and len(parts)>1:nick=parts[1];reg()
    elif cmd=='USER' and len(parts)>1:reg()
    elif cmd=='PING':send(':huddle PONG huddle '+line.split(':',1)[-1])
    elif cmd=='QUIT':return
    elif cmd=='PRIVMSG' and len(parts)>2:
     name=parts[1];body=line.split(' :',1)[1] if ' :' in line else ''
     c=db();ch=channel(c,name)
     if ch and registered:
      c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(ch['id'],uid,body,time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
      c.execute('UPDATE channels SET event_id=event_id+1 WHERE id=?',(ch['id'],));ev=c.execute('SELECT event_id FROM channels WHERE id=?',(ch['id'],)).fetchone()[0];c.execute('INSERT INTO events(channel_id,event_id,kind,message_id,payload) VALUES(?,?,?,?,?)',(ch['id'],ev,'message.created',mid,json.dumps({'message':{'id':mid,'channel_id':ch['id'],'author_id':uid,'body':body}})));c.commit()
    elif cmd=='JOIN' and len(parts)>1:
     name=parts[1];c=db();ch=channel(c,name)
     if not ch:send(':huddle 403 '+str(nick)+' '+name+' :No such channel')
     else:joined.add(name);last[ch['id']]=ch['event_id'];send(':'+str(nick)+'!user@huddle JOIN '+name);send(':huddle 353 '+str(nick)+' = '+name+' :'+str(nick));send(':huddle 366 '+str(nick)+' '+name+' :End of NAMES list')
    elif cmd in ('NAMES','WHO','TOPIC','MODE') and len(parts)>1:send(':huddle 353 '+str(nick)+' = '+parts[1]+' :'+str(nick));send(':huddle 366 '+str(nick)+' '+parts[1]+' :End of NAMES list')
    elif cmd=='LIST':send(':huddle 323 '+str(nick)+' :End of LIST')
    elif cmd=='PONG':pass
    elif cmd and registered:send(':huddle 421 '+str(nick)+' '+cmd+' :Unknown command')
 finally:
  stop=True
  if nick:
   try:db().execute('DELETE FROM irc_nicks WHERE nick=?',(nick,));db().commit()
   except:pass
  cl.close()
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
while True:
 cl,_=s.accept();threading.Thread(target=serve,args=(cl,),daemon=True).start()
