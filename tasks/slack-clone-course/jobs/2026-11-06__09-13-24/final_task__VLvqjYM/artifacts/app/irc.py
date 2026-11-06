import asyncio

async def handle(reader, writer):
    writer.close()
    await writer.wait_closed()

async def main():
    server = await asyncio.start_server(handle, '0.0.0.0', 6667)
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
