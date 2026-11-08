import asyncio,core
async def handle(reader,writer):
    user=None;nick='guest';buf=b''
    try:
        while True:
            line=await reader.readline()
            if not line:break
            s=line.decode(errors='ignore').strip(); parts=s.split(' ',2);cmd=parts[0].upper()
            if cmd=='PASS' and len(parts)>1:user=core.user_for_token(parts[1])
            elif cmd=='NICK' and len(parts)>1:nick=parts[1]
            elif cmd=='PING':
                writer.write(('PONG :'+(parts[1] if len(parts)>1 else 'ping')+'\r\n').encode());await writer.drain()
            elif cmd=='QUIT':break
            elif cmd=='USER' and user:
                for n in range(1,6):writer.write(f':huddle 00{n} {nick} :Huddle\r\n'.encode())
                writer.write(f':huddle 422 {nick} :MOTD File is missing\r\n'.encode());await writer.drain()
            elif cmd=='JOIN' and len(parts)>1 and user:
                name=parts[1].lstrip('#'); z=name.split('/',1)
                if len(z)!=2:writer.write(f':huddle 403 {nick} {parts[1]} :No such channel\r\n'.encode())
                else:
                    w=core.get_workspace(z[0]);c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=(SELECT id FROM workspaces WHERE slug=?) AND name=?',(z[0],z[1])).fetchone();c.close()
                    if not ch:writer.write(f':huddle 403 {nick} {parts[1]} :No such channel\r\n'.encode())
                    else:
                        writer.write(f':{nick}!user@localhost JOIN {parts[1]}\r\n: huddle 366 {nick} {parts[1]} :End of NAMES list\r\n'.replace(': huddle',':huddle').encode())
                await writer.drain()
    finally:writer.close();await writer.wait_closed()
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s:await s.serve_forever()
if __name__=='__main__':asyncio.run(main())
