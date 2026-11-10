import asyncio
async def handle(reader,writer):
    nick=None
    try:
        while True:
            line=await reader.readline()
            if not line: break
            s=line.decode(errors='ignore').strip()
            p=s.split(' ',1);cmd=p[0].upper()
            if cmd=='NICK' and len(p)>1:nick=p[1]
            elif cmd=='PING': writer.write(b':huddle PONG huddle\r\n');await writer.drain()
            elif cmd=='QUIT': break
            elif cmd=='PASS': pass
            elif cmd=='USER' and nick:
                for n in range(1,6): writer.write(f':huddle {n:03d} {nick} :huddle\r\n'.encode())
                writer.write(f':huddle 422 {nick} :MOTD File is missing\r\n'.encode());await writer.drain()
            elif cmd=='JOIN' and len(p)>1:
                ch=p[1].split()[0];writer.write(f':{nick}!user@localhost JOIN {ch}\r\n'.encode());await writer.drain()
    finally: writer.close();await writer.wait_closed()
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s: await s.serve_forever()
if __name__=='__main__': asyncio.run(main())
