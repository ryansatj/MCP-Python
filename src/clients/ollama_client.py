from contextlib import AbstractAsyncContextManager, AsyncExitStack
from itertools import chain
import json
import logging
from abstract.api_response import ChatResponse
from abstract.session import Session
import colorlog
from mcp.client.streamable_http import streamablehttp_client
from typing import Optional
from mcp import ClientSession, StdioServerParameters
from mcp.types import TextContent
from mcp.client.stdio import stdio_client
from typing import AsyncIterator, Self, Sequence, cast
from ollama import AsyncClient, Message, Tool

from abstract.config_container import ConfigContainer

SYSTEM_PROMPT = """
You are a helpful assistant capable of accessing external functions and engaging in casual chat.
Use the responses from these function calls to provide accurate and informative answers.
The answers should be natural and hide the fact that you are using tools to access real-time information.
Guide the user about available tools and their capabilities.
Always utilize tools to access real-time information when required.
Engage in a friendly manner to enhance the chat experience.
(IMPORTANT) Always pass the function rawly from the user, do not modify it. e.g if the user asks to get a config from 'L2 Cisco 2960X IB_1F', just pass it as is, do not change it to 'L2_Cisco_2960X_IB_1F

# Notes
- Use English in every conversation.
- Make function calls efficient, only call functions one time if not needed for multi function call.
- Ensure responses are based on the latest information available from function calls.
- Maintain an engaging, supportive, and friendly tone throughout the dialogue.
- Always highlight the potential of available tools to assist users comprehensively.
- Always pass the function rawly from the user, do not modify it. e.g if the user asks to get a config from 'L2 Cisco 2960X IB_1F', just pass it as is, do not change it to 'L2_Cisco_2960X_IB_1F'.
"""


class OllamaMCPClient(AbstractAsyncContextManager):
    def __init__(self, host: str | None = None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.DEBUG)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        formatter = colorlog.ColoredFormatter(
            "%(log_color)s%(levelname)s%(reset)s - %(message)s",
            datefmt=None,
            reset=True,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )

        console_handler.setFormatter(formatter)
        if not self.logger.hasHandlers():
            self.logger.addHandler(console_handler)

        self.client = AsyncClient(host)
        self.servers: dict[str, Session] = {}
        self.selected_server: dict[str, Session] = {}
        self.messages = []
        self.exit_stack = AsyncExitStack()
        
        self._http_connections: dict[str, tuple] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            for server_name, (streams_context, session_context) in self._http_connections.items():
                if session_context:
                    await session_context.__aexit__(None, None, None)
                if streams_context:
                    await streams_context.__aexit__(None, None, None)
            
            await self.exit_stack.aclose()
        except (ValueError, Exception):
            return

    @classmethod
    async def create(cls, config: ConfigContainer, host="http://127.0.0.1:11434") -> Self:
        """Factory method to create and initialize a client instance"""
        client = cls(host)
        await client._connect_to_multiple_servers(config)
        return client

    async def _connect_to_multiple_servers(self, config: ConfigContainer):
        for name, params in config.items():
            session, tools = await self._connect_to_server(name, params)
            self.servers[name] = Session(session=session, tools=[*tools])

        self.selected_server = self.servers

        self.logger.info(
            f"Connected to stdio servers with tools: {[cast(Tool.Function, tool.function).name for tool in self.get_tools()]}"
        )

    async def connect_to_streamable_http_server(self, server_url: str, headers: Optional[dict] = None, server_name: Optional[str] = None):
        """Connect to an MCP server running with HTTP Streamable transport"""
        if server_name is None:
            server_name = f"http_{len(self._http_connections)}"
        
        streams_context = streamablehttp_client(url=server_url, headers=headers or {})
        read_stream, write_stream, _ = await streams_context.__aenter__()

        session_context = ClientSession(read_stream, write_stream)
        session: ClientSession = await session_context.__aenter__()

        await session.initialize()

        self._http_connections[server_name] = (streams_context, session_context)

        response = await session.list_tools()
        tools = [
            Tool(
                type="function",
                function=Tool.Function(
                    name=f"{server_name}/{tool.name}",
                    description=tool.description,
                    parameters=cast(Tool.Function.Parameters, tool.inputSchema),
                ),
            )
            for tool in response.tools
        ]

        self.servers[server_name] = Session(session=session, tools=tools)
        
        if not self.selected_server:
            self.selected_server = {server_name: self.servers[server_name]}
        else:
            self.selected_server[server_name] = self.servers[server_name]

    async def _connect_to_server(
        self, name: str, server_params: StdioServerParameters
    ) -> tuple[ClientSession, Sequence[Tool]]:
        """Connect to an MCP server

        Args:
            server_script_path: Path to the server script (.py)
        """
        stdio, write = await self.exit_stack.enter_async_context(stdio_client(server_params))
        session = cast(ClientSession, await self.exit_stack.enter_async_context(ClientSession(stdio, write)))

        await session.initialize()

        response = await session.list_tools()
        tools = [
            Tool(
                type="function",
                function=Tool.Function(
                    name=f"{name}/{tool.name}",
                    description=tool.description,
                    parameters=cast(Tool.Function.Parameters, tool.inputSchema),
                ),
            )
            for tool in response.tools
        ]
        return (session, tools)

    def get_tools(self) -> list[Tool]:
        return list(chain.from_iterable(server.tools for server in self.selected_server.values()))

    def select_server(self, servers: list[str]) -> Self:
        self.selected_server = {name: server for name, server in self.servers.items() if name in servers}
        self.logger.info(f"Selected server: {list(self.selected_server.keys())}")
        return self

    async def prepare_prompt(self):
        """Clear current message and create new one"""
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    async def process_message(self, message: str, model: str | None = None) -> AsyncIterator[ChatResponse]:
        """Process a query using LLM and available tools"""
        if model is None:
            model = "qwen3:8b"  # Predefined model
        self.messages.append({"role": "user", "content": message})

        async for part in self._recursive_prompt(model):
            yield part

    async def _recursive_prompt(self, model: str) -> AsyncIterator[ChatResponse]:
        self.logger.debug("Prompting")
        
        available_tools = self.get_tools()
        
        stream = await self.client.chat(
            model=model,
            messages=self.messages,
            tools=available_tools,
            stream=True,
        )

        assistant_content = ""
        all_tool_calls = []
        
        # Collect all parts of the response
        async for part in stream:
            if part.message.content:
                assistant_content += part.message.content
                yield ChatResponse(role="assistant", content=part.message.content)
            elif part.message.tool_calls:
                self.logger.debug(f"Calling tool: {part.message.tool_calls}")
                all_tool_calls.extend(part.message.tool_calls)

        if all_tool_calls:
            if assistant_content:
                assistant_message = {"role": "assistant", "content": assistant_content}
            else:
                tool_names = [tc.function.name for tc in all_tool_calls]
                assistant_message = {
                    "role": "assistant", 
                    "content": f"I'll call the following tools: {', '.join(tool_names)}"
                }
                
            self.messages.append(assistant_message)
            
            tool_results = await self._tool_call(all_tool_calls)
            
            if len(tool_results) > 1:
                # Combine multiple results into one comprehensive message
                combined_content = f"MULTIPLE TOOL RESULTS (Total: {len(tool_results)}):\n\n"
                combined_content += "\n\n".join(tool_results)
                combined_content += f"\n\nPlease analyze and correlate ALL {len(tool_results)} tool results above."
                
                tool_message = {"role": "tool", "content": combined_content}
                self.messages.append(tool_message)
                yield ChatResponse(role="tool", content=combined_content)
            else:
                # Single tool result
                for result in tool_results:
                    tool_message = {"role": "tool", "content": result}
                    self.messages.append(tool_message)
                    yield ChatResponse(role="tool", content=result)
            
            async for part in self._recursive_prompt(model):
                yield part

    async def _tool_call(self, tool_calls: Sequence[Message.ToolCall]) -> list[str]:
        """Execute tool calls and return formatted results"""
        results = []
        for i, tool in enumerate(tool_calls):
            split = tool.function.name.split("/")
            server_name = split[0]
            tool_name = split[1]
            tool_args = tool.function.arguments

            if server_name in self.selected_server:
                session = self.selected_server[server_name].session
            else:
                session = list(self.selected_server.values())[0].session

            try:
                result = await session.call_tool(tool_name, dict(tool_args))
                self.logger.debug(f"Tool call result for {tool.function.name}: {result.content}")
                
                result_text = cast(TextContent, result.content[0]).text
                
                formatted_result = f"=== TOOL RESULT #{i+1} ===\nTool: {tool.function.name}\nArguments: {tool_args}\nResult:\n{result_text}\n=========================="
                results.append(formatted_result)
                
            except Exception as e:
                self.logger.error(f"Tool call error for {tool.function.name}: {e}")
                error_result = f"=== TOOL ERROR #{i+1} ===\nTool: {tool.function.name}\nArguments: {tool_args}\nError: {str(e)}\n=========================="
                results.append(error_result)

        return results