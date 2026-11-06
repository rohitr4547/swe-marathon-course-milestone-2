import asyncio,re,core
clients=[]
nicks=set()
async def send(w,line):
    try:w.write((line+'\r\n').encode());await w.drain();return True
    except:return False
def channel_name(cid):
    c=core.connect();r=c.execute('SELECT c.name,w.slug FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE c.id=?',(cid,)).fetchone();c.close()
    return '#'+r['slug']+'/'+r['name'] if r else None
async def deliver(state):
    while not state['closed']:
        for cid in list(state['joined']):
            for e in core.events_after(cid,state['last'].get(cid,0)):
                state['last'][cid]=e['event_id']
                if e.get('type') not in ('message.created','message.reply'):continue
                m=e.get('message') or {}
                if m.get('author_id')==state.get('user_id'):continue
                name=channel_name(cid)
                if not name:continue
                c=core.connect();u=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone();c.close()
                nick=u['username'] if u else 'user'
                if not await send(state['writer'],f":{nick}!{nick}@localhost PRIVMSG {name} :{m.get('body','')}"):
                    state['closed']=True;break
        await asyncio.sleep(.05)
async def handle(reader,writer):
    token=None;nick=None;user=None;registered=False
    state={'writer':writer,'joined':set(),'last':{},'closed':False,'user_id':None}
    clients.append(state);poll=asyncio.create_task(deliver(state))
    try:
        while True:
            raw=await reader.readline()
            if not raw:break
            line=raw.decode(errors='ignore').rstrip('\r\n')
            if not line:continue
            p=line.split(' ',2);cmd=p[0].upper();args=p[1:]
            if cmd=='PASS':token=args[0] if args else ''
            elif cmd=='NICK':
                new=args[0] if args else 'guest'
                if new in nicks and new!=nick:await send(writer,f':huddle 433 {nick or "*"} {new} :Nickname is already in use')
                else:
                    if nick:nicks.discard(nick)
                    nick=new;nicks.add(nick)
            elif cmd=='USER':
                user=core.user_for_token(token)
                if not user:await send(writer,':huddle 464 * :Password incorrect');break
                state['user_id']=user['id'];registered=True
                for code in ('001','002','003','004','005'):await send(writer,f':huddle {code} {nick} :Huddle')
                await send(writer,f':huddle 422 {nick} :MOTD File is missing')
            elif cmd=='PING':await send(writer,':huddle PONG '+(args[0] if args else 'huddle'))
            elif cmd=='PONG':pass
            elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'):
                if cmd=='NAMES':
                    target=args[0] if args else '*';await send(writer,f':huddle 353 {nick} = {target} :{nick}');await send(writer,f':huddle 366 {nick} {target} :End of NAMES list')
                elif cmd=='LIST':await send(writer,f':huddle 321 {nick} Channel :Users Name')
                elif cmd=='WHO':await send(writer,f':huddle 315 {nick} {args[0] if args else "*"} :End of WHO list')
            elif cmd=='JOIN' and args:
                name=args[0];m=re.fullmatch(r'#([^/]+)/(.+)',name);ch=None
                if m:
                    c=core.connect();ch=c.execute('SELECT c.* FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(m.group(1),m.group(2))).fetchone();c.close()
                if not ch:await send(writer,f':huddle 403 {nick} {name} :No such channel');continue
                cid=ch['id'];state['joined'].add(cid);state['last'][cid]=core.head_event_id(cid)
                with core.tx() as c:
                    if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,user['id'])).fetchone():
                        core.add_channel_member(c,cid,user['id']);core.emit_event(c,cid,'member.joined',{'user_id':user['id']})
                await send(writer,f':{nick}!irc@localhost JOIN {name}');await send(writer,f':huddle 353 {nick} = {name} :{nick}');await send(writer,f':huddle 366 {nick} {name} :End of NAMES list')
            elif cmd=='PRIVMSG' and len(p)>2 and user:
                target=p[1];text=p[2].lstrip(':');m=re.fullmatch(r'#([^/]+)/(.+)',target)
                if not m:continue
                c=core.connect();ch=c.execute('SELECT c.* FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(m.group(1),m.group(2))).fetchone();c.close()
                if not ch:continue
                cid=ch['id'];state['joined'].add(cid)
                with core.tx() as c:
                    cur=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(cid,user['id'],text,core.now_iso()))
                    r=c.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone();frame=core.emit_event(c,cid,'message.created',{'message':core.message_to_json(r)});c.execute('UPDATE messages SET event_id=? WHERE id=?',(frame['event_id'],cur.lastrowid))
                state['last'][cid]=frame['event_id']
            elif cmd=='QUIT':break
            elif registered and cmd not in ('CAP','PASS','NICK','USER'):await send(writer,f':huddle 421 {nick} {cmd} :Unknown command')
    finally:
        state['closed']=True;poll.cancel()
        try:await poll
        except asyncio.CancelledError:pass
        if nick:nicks.discard(nick)
        try:clients.remove(state)
        except ValueError:pass
        writer.close()
        try:await writer.wait_closed()
        except:pass
async def main():return await asyncio.start_server(handle,'0.0.0.0',6667)
if __name__=='__main__':
    async def runner():
        server=await main()
        async with server:await server.serve_forever()
    asyncio.run(runner())
