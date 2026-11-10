import asyncio
async def client(reader, writer):
    try:
        while True:
            line=await reader.readline()
            if not line: break
            if line.upper().startswith(b'PING'):
                writer.write(b':huddle PONG huddle\r\n')
                await writer.drain()
            elif line.upper().startswith(b'QUIT'):
                break
    finally:
        writer.close()
        await writer.wait_closed()
async def main():
    server=await asyncio.start_server(client,'0.0.0.0',6667)
    async with server:
        await server.serve_forever()
if __name__=='__main__':
    asyncio.run(main())
