# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx",
#     "mcp[cli]",
# ]
# ///

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("rag")

# Before trying to use this tool, make sure you have a local RAG server running on port 8001.
@mcp.tool()
async def local_rag_query(query: str) -> str:
    """
    Using RAG server, to enhance the query with additional context and knowledge, only use this when prompted (High Computational Cost).

    🚫 DO NOT call or mention the `local_rag_query` tool unless:
    - The user explicitly says “Use RAG” in their prompt.
    - Or the frontend button “Use RAG” has triggered the tool.
    ✅ NEVER explain or narrate plans to use RAG unless the user explicitly asks for it.
    ✅ If the user requests an interface, health, or problem check — go straight to the relevant Zabbix tool. DO NOT route through RAG.

    Args:
        query (str): The query to be enhanced with additional context and knowledge.

    Returns:
        str: The enhanced query result from the RAG server.
    """
    try:
        async with httpx.AsyncClient(timeout=300.0) as client: 
            res = await client.post(
                "http://localhost:8020/query/local",
                json={"query": query},
            )
            res.raise_for_status()
            data = res.json()
            return data.get("result", "No result found")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return f"Error executing tool local_rag_query: {e}\nTraceback:\n{tb}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
