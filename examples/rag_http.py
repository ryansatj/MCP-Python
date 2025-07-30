import asyncio
import json
import sys
from typing import Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import logging
from contextlib import asynccontextmanager

from abstract.config_container import ConfigContainer
from clients.lightrag_client import RagMCPClient

# Global client instance
rag_client: Optional[RagMCPClient] = None

# Request/Response models
class ChatRequest(BaseModel):
    message: str
    stream: bool = True

class ChatResponse(BaseModel):
    role: str
    content: str

class ServerSelectionRequest(BaseModel):
    servers: list[str]

class ToolInfo(BaseModel):
    name: str
    description: str

# Initialize the RAG client on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag_client
    
    # Get config from command line args or use default
    if len(sys.argv) >= 2:
        server_input = sys.argv[1]
        additional_http_urls = sys.argv[2:] if len(sys.argv) > 2 else []
        
        if server_input.startswith("http"):
            # HTTP-only mode
            rag_client = RagMCPClient()
            await rag_client.__aenter__()
            await rag_client.connect_to_streamable_http_server(server_input)
            
            for i, url in enumerate(additional_http_urls):
                if url.startswith("http"):
                    await rag_client.connect_to_streamable_http_server(url, server_name=f"http_{i+1}")
        else:
            # Config file mode
            config = ConfigContainer.form_file(server_input)
            rag_client = await RagMCPClient.create(config)
            
            # Connect extra HTTP servers from config
            http_servers = config.get_http_servers()
            for http_server in http_servers:
                await rag_client.connect_to_streamable_http_server(
                    http_server.url,
                    server_name=http_server.name
                )
            
            # Connect extra HTTP passed via CLI
            for i, url in enumerate(additional_http_urls):
                if url.startswith("http"):
                    await rag_client.connect_to_streamable_http_server(url, server_name=f"http_extra_{i}")
    else:
        # Default mode - just RAG without external servers
        rag_client = RagMCPClient()
        await rag_client.__aenter__()
    
    await rag_client.prepare_prompt()
    print("✅ RAG Client initialized and ready!")
    
    yield
    
    # Cleanup
    if rag_client:
        await rag_client.__aexit__(None, None, None)

# Create FastAPI app
app = FastAPI(
    title="RAG MCP Chat Server",
    description="HTTP API for RAG-enabled MCP client",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add this function to your HTTP server

def clean_rag_response(response_text: str) -> str:
    """Clean RAG response to show only the final answer"""
    
    # Look for the tool result separator (26 equals signs)
    separator = "=" * 26
    
    if separator in response_text:
        # Split by separator and get everything after the last one
        parts = response_text.split(separator)
        if len(parts) > 1:
            clean_content = parts[-1].strip()
            
            # Return clean content if it's substantial
            if clean_content and len(clean_content) > 10:
                return clean_content
    
    # Alternative: Look for patterns and clean them
    lines = response_text.split('\n')
    cleaned_lines = []
    in_tool_section = False
    
    for line in lines:
        # Detect start of tool execution
        if (line.startswith('TOOL_CALL:') or 
            line.startswith('=== TOOL RESULT') or 
            line.startswith('=== TOOL ERROR')):
            in_tool_section = True
            continue
        
        # Detect end of tool execution
        if line.strip() == separator:
            in_tool_section = False
            continue
        
        # Skip lines in tool section
        if in_tool_section:
            continue
        
        # Keep other lines
        cleaned_lines.append(line)
    
    cleaned = '\n'.join(cleaned_lines).strip()
    return cleaned if cleaned else response_text

# Then modify your chat endpoint:

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """Main chat endpoint"""
    global rag_client
    
    if rag_client is None:
        raise HTTPException(status_code=500, detail="RAG client not initialized")
    
    try:
        if request.stream:
            # For streaming, you'll need to implement the filter in the stream
            async def generate_response():
                if rag_client is not None:
                    full_response = ""
                    async for part in rag_client.process_message(request.message):
                        full_response += part["content"]
                    
                    # Clean the full response
                    cleaned_response = clean_rag_response(full_response)
                    yield f"data: {json.dumps({'role': 'assistant', 'content': cleaned_response})}\n\n"
                    yield "data: [DONE]\n\n"
            
            return StreamingResponse(
                generate_response(),
                media_type="text/plain",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream"
                }
            )
        else:
            # Non-streaming response
            full_response = ""
            async for part in rag_client.process_message(request.message):
                full_response += part["content"]
            
            # Clean the response
            cleaned_response = clean_rag_response(full_response)
            
            return ChatResponse(role="assistant", content=cleaned_response)
            
    except Exception as e:
        if rag_client is not None:
            rag_client.logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"message": "RAG MCP Chat Server is running", "endpoint": "/chat"}

@app.post("/clear")
async def clear_prompt():
    """Clear the conversation history"""
    global rag_client
    
    if rag_client is None:
        raise HTTPException(status_code=500, detail="RAG client not initialized")
    
    try:
        await rag_client.prepare_prompt()
        return {"message": "Prompt cleared successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/servers")
async def list_servers():
    """List all available servers"""
    global rag_client
    
    if rag_client is None:
        raise HTTPException(status_code=500, detail="RAG client not initialized")
    
    return {
        "all_servers": list(rag_client.servers.keys()),
        "selected_servers": list(rag_client.selected_server.keys())
    }

@app.post("/servers/select")
async def select_servers(request: ServerSelectionRequest):
    """Select specific servers"""
    global rag_client
    
    if rag_client is None:
        raise HTTPException(status_code=500, detail="RAG client not initialized")
    
    try:
        valid_servers = [s for s in request.servers if s in rag_client.servers]
        if not valid_servers:
            raise HTTPException(
                status_code=400, 
                detail=f"No valid servers found. Available: {list(rag_client.servers.keys())}"
            )
        
        rag_client.select_server(valid_servers)
        return {
            "message": f"Selected servers: {valid_servers}",
            "selected_servers": valid_servers
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/tools")
async def list_tools():
    """List all available tools"""
    global rag_client
    
    if rag_client is None:
        raise HTTPException(status_code=500, detail="RAG client not initialized")
    
    try:
        tools = rag_client.get_tools()
        tool_list = []
        
        for tool in tools:
            tool_info = {
                "name": tool.function,
                "description": getattr(tool.function, 'description', 'No description available'),
                "parameters": getattr(tool.function, 'parameters', {})
            }
            tool_list.append(tool_info)
        
        return {"tools": tool_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    global rag_client
    
    return {
        "status": "healthy" if rag_client else "unhealthy",
        "client_initialized": rag_client is not None
    }

# Add a simple HTML interface for testing
@app.get("/ui")
async def chat_ui():
    """Simple web UI for testing"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>RAG Chat Interface</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            #chat-container { border: 1px solid #ccc; height: 400px; overflow-y: auto; padding: 10px; margin-bottom: 20px; }
            #message-input { width: 70%; padding: 10px; }
            #send-btn { width: 25%; padding: 10px; }
            .message { margin: 10px 0; padding: 5px; }
            .user { background-color: #e3f2fd; }
            .assistant { background-color: #f3e5f5; }
            .tool { background-color: #e8f5e8; }
        </style>
    </head>
    <body>
        <h1>RAG MCP Chat Interface</h1>
        <div id="chat-container"></div>
        <div>
            <input type="text" id="message-input" placeholder="Type your message here..." />
            <button id="send-btn" onclick="sendMessage()">Send</button>
        </div>
        <div>
            <button onclick="clearChat()">Clear Chat</button>
            <button onclick="listTools()">List Tools</button>
            <button onclick="listServers()">List Servers</button>
        </div>
        
        <script>
            const chatContainer = document.getElementById('chat-container');
            const messageInput = document.getElementById('message-input');
            
            function addMessage(role, content) {
                const div = document.createElement('div');
                div.className = `message ${role}`;
                div.innerHTML = `<strong>${role}:</strong> ${content}`;
                chatContainer.appendChild(div);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }
            
            async function sendMessage() {
                const message = messageInput.value.trim();
                if (!message) return;
                
                addMessage('user', message);
                messageInput.value = '';
                
                try {
                    const response = await fetch('/chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: message, stream: false })
                    });
                    
                    const data = await response.json();
                    addMessage('assistant', data.content);
                } catch (error) {
                    addMessage('error', 'Failed to send message: ' + error.message);
                }
            }
            
            async function clearChat() {
                try {
                    await fetch('/clear', { method: 'POST' });
                    chatContainer.innerHTML = '';
                    addMessage('system', 'Chat cleared');
                } catch (error) {
                    addMessage('error', 'Failed to clear chat: ' + error.message);
                }
            }
            
            async function listTools() {
                try {
                    const response = await fetch('/tools');
                    const data = await response.json();
                    addMessage('system', `Available tools: ${data.tools.map(t => t.name).join(', ')}`);
                } catch (error) {
                    addMessage('error', 'Failed to list tools: ' + error.message);
                }
            }
            
            async function listServers() {
                try {
                    const response = await fetch('/servers');
                    const data = await response.json();
                    addMessage('system', `Servers - All: ${data.all_servers.join(', ')} | Selected: ${data.selected_servers.join(', ')}`);
                } catch (error) {
                    addMessage('error', 'Failed to list servers: ' + error.message);
                }
            }
            
            messageInput.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    sendMessage();
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)



# Import HTMLResponse
from fastapi.responses import HTMLResponse

def main():
    """Run the HTTP server"""
    print("Starting RAG MCP HTTP Server...")
    print("Usage: python http_rag_server.py [config.json|http_url] [additional_http_urls...]")
    print("Server will be available at: http://localhost:8030")
    print("Chat endpoint: http://localhost:8030/chat")
    print("Web UI: http://localhost:8030/ui")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8030,
        log_level="info"
    )

if __name__ == "__main__":
    main()