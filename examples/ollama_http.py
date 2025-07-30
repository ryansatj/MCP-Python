import asyncio
import os
from typing import List
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from abstract.config_container import ConfigContainer
from clients.ollama_client import OllamaMCPClient
from dotenv import load_dotenv

load_dotenv()
HTTP_PORT = int(os.getenv("HTTP_PORT", "8000"))

import re

def remove_thinking_blocks(text: str) -> str:
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()


async def build_client(server_input: str, extra_urls: List[str]) -> OllamaMCPClient:
    if server_input.startswith("http"):
        client = OllamaMCPClient()
        await client.__aenter__()
        await client.connect_to_streamable_http_server(server_input)

        for i, url in enumerate(extra_urls):
            if url.startswith("http"):
                await client.connect_to_streamable_http_server(url, server_name=f"http_{i+1}")
    else:
        config = ConfigContainer.form_file(server_input)
        client = await OllamaMCPClient.create(config)
        await client.__aenter__()

        for http_server in config.get_http_servers():
            await client.connect_to_streamable_http_server(http_server.url, server_name=http_server.name)

        for i, url in enumerate(extra_urls):
            if url.startswith("http"):
                await client.connect_to_streamable_http_server(url, server_name=f"http_extra_{i}")

    await client.prepare_prompt()
    return client

def build_app(client: OllamaMCPClient) -> FastAPI:
    app = FastAPI(title="OllamaMCP HTTP Interface")

    # ✅ Allow frontend to talk to backend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],  # or ["*"] for any origin
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class ChatRequest(BaseModel):
        message: str
        stream: bool = False

    @app.get("/servers")
    async def list_servers():
        return {
            "all": list(client.servers.keys()),
            "selected": list(client.selected_server.keys())
        }

    @app.post("/chat")
    async def chat(req: ChatRequest):
        if req.stream:
            async def gen():
                async for part in client.process_message(req.message):
                    if part["role"] == "assistant":
                        filtered = remove_thinking_blocks(part["content"])
                        yield filtered
            return StreamingResponse(gen(), media_type="text/event-stream")

        answer = []
        async for part in client.process_message(req.message):
            if part["role"] == "assistant":
                filtered = remove_thinking_blocks(part["content"])
                answer.append(filtered)
        return {"reply": "".join(answer)}

    return app

async def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  uv run examples/ollama_http.py <server.json | http_url> [http_url2 ...]")
        sys.exit(1)

    server_input = sys.argv[1]
    additional_http_urls = sys.argv[2:] if len(sys.argv) > 2 else []

    client = await build_client(server_input, additional_http_urls)

    app = build_app(client)
    config = uvicorn.Config(app, host="0.0.0.0", port=HTTP_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
