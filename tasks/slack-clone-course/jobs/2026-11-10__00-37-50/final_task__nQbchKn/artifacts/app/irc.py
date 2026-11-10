import asyncio
async def main():
 s=await asyncio.start_server(lambda r,w:w.close(),'0.0.0.0',6667)
 async with s:await s.serve_forever()
asyncio.run(main())
