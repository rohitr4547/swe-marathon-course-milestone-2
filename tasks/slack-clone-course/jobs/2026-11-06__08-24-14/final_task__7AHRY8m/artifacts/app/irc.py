import asyncio
import core

NICKS=set()
NLOCK=asyncio.Lock()
CLIENTS={}

async def send(w,line):
    try:
        w.write((line.rstrip('\r\n')+'\r\n').encode()); await w.drain()
    except: pass

async def registration(state,w):
    if state.get('registered'): return True
    if not state.get('nick') or not state.get('user'): return False
    u=core.user_for_token(state.get('pass',''))
    if not u:
        await send(w,':huddle 464 * :Password incorrect')
        state['closing']=True
        return False
    async with NLOCK:
        nick=state['nick']
        if nick.lower() in NICKS:
            await send(w,f':huddle 433 * {nick} :Nickname is already in use')
            state['nick']=None
            return False
        NICKS.add(nick.lower())
    state['account']=u; state['registered']=True; CLIENTS[id(w)]=state
    nick=state['nick']
    for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1.0 oi'),('005','CHANTYPES=# :are supported by this server')]:
        await send(w,f':huddle {code} {nick} :{text}')
    await send(w,f':huddle 422 {nick} :MOTD File is missing')
    return True

def lookup_channel(name):
    if not isinstance(name,str) or not name.startswith('#') or '/' not in name[1:]: return None
    slug,cname=name[1:].split('/',1)
    w=core.get_workspace(slug)
    if not w:return None
    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone(); c.close()
    return ch

async def deliver(st,w):
    while not st.get('closing'):
        for cid,name in list(st.get('irc_channels',{}).items()):
            last=st['event_heads'].get(cid,0)
            for ev in core.events_after(cid,last):
                st['event_heads'][cid]=ev['event_id']
                if ev.get('type')!='message.created': continue
                m=ev.get('message') or {}
                if m.get('author_id')==st['account']['id']: continue
                au=m.get('author') or {}
                nick=au.get('username','user')
                await send(w,f':{nick}!{nick}@huddle PRIVMSG {name} :{m.get("body","")}')
        await asyncio.sleep(.05)

async def do_join(st,w,name):
    ch=lookup_channel(name)
    if not ch:
        await send(w,f':huddle 403 {st["nick"]} {name} :No such channel'); return
    uid=st['account']['id']
    role=core.member_role(ch['workspace_id'],uid)
    if not role:
        with core.tx() as c: core.add_workspace_member(c,ch['workspace_id'],uid,'member')
    with core.tx() as c: core.add_channel_member(c,ch['id'],uid)
    st.setdefault('channels',set()).add(ch['id'])
    st.setdefault('irc_channels',{})[ch['id']]=name
    st.setdefault('event_heads',{})[ch['id']]=core.head_event_id(ch['id'])
    prefix=f':{st["nick"]}!{st["account"]["username"]}@huddle'
    await send(w,f'{prefix} JOIN {name}')
    names=','.join(x['username'] for x in core.channel_members(ch['id'])) if hasattr(core,'channel_members') else st['nick']
    await send(w,f':huddle 353 {st["nick"]} = {name} :{names}')
    await send(w,f':huddle 366 {st["nick"]} {name} :End of /NAMES list')

async def handle(reader,writer):
    st={'nick':None,'user':None,'pass':'','registered':False,'closing':False,'channels':set(),'irc_channels':{},'event_heads':{}}
    delivery=asyncio.create_task(deliver(st,writer))
    try:
        while not reader.at_eof() and not st['closing']:
            raw=await reader.readline()
            if not raw: break
            line=raw.decode(errors='replace').strip('\r\n')
            if not line: continue
            parts=line.split(' ',1); cmd=parts[0].upper(); arg=parts[1] if len(parts)>1 else ''
            if cmd=='PASS': st['pass']=arg.lstrip(':').strip()
            elif cmd=='NICK':
                n=arg.strip().lstrip(':')
                if st.get('registered'):
                    async with NLOCK:
                        if n.lower() in NICKS and n.lower()!=st['nick'].lower():
                            await send(writer,f':huddle 433 {st["nick"]} {n} :Nickname is already in use'); continue
                        NICKS.discard(st['nick'].lower()); NICKS.add(n.lower())
                    st['nick']=n
                else: st['nick']=n
            elif cmd=='USER': st['user']=arg.split()[0] if arg.split() else ''
            elif cmd=='QUIT':
                st['closing']=True; break
            elif cmd=='PING':
                tok=arg[1:] if arg.startswith(':') else arg
                await send(writer,f':huddle PONG huddle :{tok}')
            elif cmd=='JOIN':
                if not st.get('registered'): continue
                await do_join(st,writer,arg.split()[0] if arg else '')
            elif cmd=='PRIVMSG':
                if not st.get('registered'): continue
                z=arg.split(' :',1)
                if len(z)!=2: continue
                name,body=z
                ch=lookup_channel(name.strip())
                if not ch or ch['id'] not in st.get('channels',set()): continue
                try: core.create_message(st['account'],ch['id'],body)
                except: pass
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
                if cmd=='NAMES' and st.get('registered') and arg:
                    await do_join(st,writer,arg.split()[0])
            else:
                await send(writer,f':huddle 421 {st.get("nick") or "*"} {cmd} :Unknown command')
            if not st.get('registered') and not st.get('closing'):
                await registration(st,writer)
    finally:
        delivery.cancel()
        if st.get('nick'):
            async with NLOCK: NICKS.discard(st['nick'].lower())
        CLIENTS.pop(id(writer),None)
        try: writer.close(); await writer.wait_closed()
        except: pass

async def main():
    server=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with server: await server.serve_forever()

if __name__=='__main__': asyncio.run(main())
