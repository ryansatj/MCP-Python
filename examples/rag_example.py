import asyncio
import re
import sys
from abstract.config_container import ConfigContainer
from clients.lightrag_client import RagMCPClient


async def main():
    if len(sys.argv) < 2:
        print("Usage: uv run example/rag_main.py <server_config.json> [http_url1] [http_url2] ...")
        print("       uv run example/rag_main.py <http_url>")
        sys.exit(1)

    server_input = sys.argv[1]
    additional_http_urls = sys.argv[2:] if len(sys.argv) > 2 else []

    if server_input.startswith("http"):
        async with RagMCPClient() as client:
            await client.connect_to_streamable_http_server(server_input)

            for i, url in enumerate(additional_http_urls):
                if url.startswith("http"):
                    await client.connect_to_streamable_http_server(url, server_name=f"http_{i+1}")

            await client.prepare_prompt()
            await run_chat_loop(client)

    else:
        config = ConfigContainer.form_file(server_input)
        async with await RagMCPClient.create(config) as client:
            # Connect extra HTTP servers from config
            http_servers = config.get_http_servers()
            for http_server in http_servers:
                await client.connect_to_streamable_http_server(
                    http_server.url,
                    server_name=http_server.name
                )

            # Connect extra HTTP passed via CLI
            for i, url in enumerate(additional_http_urls):
                if url.startswith("http"):
                    await client.connect_to_streamable_http_server(url, server_name=f"http_extra_{i}")

            await client.prepare_prompt()
            await run_chat_loop(client)


async def run_chat_loop(client: RagMCPClient):
    print("\n✅ RAG Client Ready!")
    print("Type your message or 'quit' to exit.")
    print("Commands: clear, server <name>, list_servers, list_tools")

    while True:
        try:
            query = input("\n💬 Chat: ").strip()

            match query.lower():
                case "quit":
                    break
                case "clear":
                    await client.prepare_prompt()
                    print("Prompt cleared.")
                    continue
                case "list_servers":
                    print(f"All servers: {list(client.servers.keys())}")
                    print(f"Selected: {list(client.selected_server.keys())}")
                    continue
                case "list_tools":
                    tools = client.get_tools()
                    print("Available tools:")
                    for tool in tools:
                        print(f"- {tool.function}")
                    continue
                case server_command if server_match := re.match(r"server (\w+)", server_command):
                    server_name = server_match.group(1)
                    if server_name in client.servers:
                        client.select_server([server_name])
                        print(f"✅ Switched to server: {server_name}")
                    else:
                        print(f"⚠️ Server '{server_name}' not found.")
                    continue
                case server_command if server_match := re.match(r"server (.+)", server_command):
                    server_names = [name.strip() for name in server_match.group(1).split(',')]
                    valid = [s for s in server_names if s in client.servers]
                    if valid:
                        client.select_server(valid)
                        print(f"✅ Selected servers: {valid}")
                    else:
                        print(f"⚠️ No valid servers. Available: {list(client.servers.keys())}")
                    continue

            async for part in client.process_message(query):
                print(part["content"], end="", flush=True)

        except Exception as e:
            client.logger.error(e)
            print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
