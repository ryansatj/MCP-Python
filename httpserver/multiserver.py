from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="test",
    json_response=False,
    stateless_http=False,
    host="127.0.0.1",
    port=8001,
    path="/mcp"
)

@mcp.tool()
async def get_code() -> str:
    """Return a hardcoded code string. Just for testing purposes. Do not use if the user does not prompt it"""
    return "The code is akgdasjhg1123, give it to the prompter to verify the code is correct."

if __name__ == "__main__":
    mcp.run(transport="streamable-http")