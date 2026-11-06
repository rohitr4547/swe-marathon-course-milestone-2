import asyncio, core, json, time
clients=set()
nicks=set()
def send(c,line):
    try:c.writer.write((line+'\r\n').encode())
    except:pass
def channel_by_name(name):
    if not name.startswith('#') or '/' not in name:return None
    slug,cn=name[1:].split('/',1)
    w=core.get_workspace(slug)
    if not w:return None
    c=core.connect(); r=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cn)).fetchone();c.close()
    return r
def numeric(c,code,text,params=''):
    send(c,f':huddle {code} {c.nick or "*"} {params} :{text}')
async def commit(c,ch,body):
    with core.tx() as db:
        db.execute('INSERT INTO messages(channel_id,author_id,body,created_at,mentions) VALUES(?,?,?,?,?)',(ch['id'],c.user['id'],body,core.now_iso(),'[]'))
        r=db.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone()
        core.emit_event(db,ch['id'],'message.created',{'message':{'id':r['id'],'channel_id':ch['id'],'author_id':r['author_id'],'body':r['body'],'parent_id':None,'created_at':r['created_at'],'edited_at':None,'files':[],'reactions':[],'mentions':[],'reply_count':0}})
async def deliver(c):
    while not c.closed:
        for ch_id,last in list(c.joined.items()):
            for e in core.events_after(ch_id,last):
                c.joined[ch_id]=e['event_id']
                if e['type'] not in ('message.created','message.reply'):continue
                m=e.get('message') or {}
                if m.get('author_id')==c.user['id'] and m.get('body') in c.sent_bodies: 
                    c.sent_bodies.discard(m.get('body')); continue
                u=core.user_for_token(c.token)
                if not u:continue
                cc=core.connect(); ar=cc.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone();cc.close()
                name=c.names.get(ch_id,'#unknown')
                send(c,f":{ar['username'] if ar else 'user'}!{ar['username'] if ar else 'user'}@huddle PRIVMSG {name} :{m.get('body','')}")
        await asyncio.sleep(.05)
async def handle(reader,writer):
    c=type('IRC',(),{})();c.writer=writer;c.token=None;c.user=None;c.nick=None;c.userline=None;c.joined={};c.names={};c.sent_bodies=set();c.closed=False
    clients.add(c); task=asyncio.create_task(deliver(c))
    try:
        while True:
            raw=await reader.readline()
            if not raw:break
            line=raw.decode(errors='ignore').strip('\r\n')
            if not line:continue
            bits=line.split(' ',1); cmd=bits[0].upper(); rest=bits[1] if len(bits)>1 else ''
            if cmd=='PASS': c.token=rest.strip()
            elif cmd=='NICK':
                nick=rest.strip()
                if nick in nicks and nick!=c.nick:
                    numeric(c,'433','Nickname is already in use',f'* {nick}');continue
                if c.nick:nicks.discard(c.nick)
                c.nick=nick;nicks.add(nick)
                if c.userline and not c.user:
                    c.user=core.user_for_token(c.token)
                    if not c.user:
                        numeric(c,'464','Password incorrect')
                        break
                    for x in ('001','002','003','004','005'):
                        numeric(c,x,'Welcome',c.nick)
                    numeric(c,'422','MOTD File is missing')
            elif cmd=='USER':
                c.userline=rest
                if c.nick and not c.user:
                    c.user=core.user_for_token(c.token)
                    if not c.user:
                        numeric(c,'464','Password incorrect')
                        break
                    for x in ('001','002','003','004','005'):
                        numeric(c,x,'Welcome',c.nick)
                    numeric(c,'422','MOTD File is missing')
            elif cmd=='PING':
                tok=rest[1:] if rest.startswith(':') else rest;send(c,f':huddle PONG huddle :{tok}')
            elif cmd=='PONG':pass
            elif cmd=='QUIT':break
            elif cmd=='JOIN':
                if not c.nick or not c.userline:continue
                if not c.user:
                    c.user=core.user_for_token(c.token)
                    if not c.user:
                        numeric(c,'464','Password incorrect');break
                name=rest.split()[0] if rest else ''
                ch=channel_by_name(name)
                if not ch:
                    numeric(c,'403','No such channel',name);continue
                role=core.member_role(ch['workspace_id'],c.user['id'])
                if not role:
                    w=core.connect().execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                    if not w or w['join_mode']!='open':
                        numeric(c,'403','No such channel',name);continue
                with core.tx() as db: core.add_channel_member(db,ch['id'],c.user['id'])
                c.joined[ch['id']]=core.head_event_id(ch['id']);c.names[ch['id']]=name
                send(c,f":{c.nick}!{c.user['username']}@huddle JOIN {name}")
                cc=core.connect(); ns=cc.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();cc.close()
                numeric(c,'353','',f'= {name} :{" ".join(x['username'] for x in ns)}');numeric(c,'366','End of /NAMES list',name)
            elif cmd=='PRIVMSG':
                if not c.user:c.user=core.user_for_token(c.token)
                if not c.user:continue
                target,body=(rest.split(' :',1)+[''])[:2] if ' :' in rest else (rest.split()[0],rest.split(maxsplit=1)[1] if len(rest.split())>1 else '')
                ch=channel_by_name(target)
                if not ch or ch['id'] not in c.joined: numeric(c,'404','Cannot send to channel',target);continue
                c.sent_bodies.add(body);await commit(c,ch,body)
            elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'):pass
            else:numeric(c,'421','Unknown command',cmd)
    finally:
        c.closed=True;task.cancel()
        if c.nick:nicks.discard(c.nick)
        clients.discard(c);writer.close();await writer.wait_closed()
async def main():
    core.init_db();srv=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with srv:await srv.serve_forever()
if __name__=='__main__':asyncio.run(main())
