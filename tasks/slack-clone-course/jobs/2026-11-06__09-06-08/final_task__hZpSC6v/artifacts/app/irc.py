import asyncio
async def handle(r,w):
    try:
        while await r.readline():
            pass
    finally:
        w.close()
        await w.wait_closed()
async def main():
    server=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with server:
        await server.serve_forever()
asyncio.run(main())
