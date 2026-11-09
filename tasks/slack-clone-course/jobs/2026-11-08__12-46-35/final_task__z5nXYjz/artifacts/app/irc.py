import asyncio
async def handle(r,w): w.close(); await w.wait_closed()
async def main(): await asyncio.start_server(handle,'0.0.0.0',6667); await asyncio.Event().wait()
if __name__=='__main__': asyncio.run(main())
