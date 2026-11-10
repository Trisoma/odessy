"""A real stdio MCP server proving only explicitly granted credentials reach it."""
import asyncio
import os
from mcp.server import MCPServer
server = MCPServer('credential-fixture')
@server.tool(name='credential_status')
def credential_status() -> dict:
    return {
        'authenticated': os.environ.get('FIXTURE_TOKEN') == 'server-credential-literal',
        'undeclared_absent': 'UNDECLARED_TOKEN' not in os.environ,
        'control_absent': 'ODESSY_TOOL_CONTROL_TOKEN' not in os.environ,
        'bridge_absent': 'ODESSY_BRIDGE_TOKEN' not in os.environ,
        'provider_absent': 'ODESSY_HARNESS_PROVIDER_KEY' not in os.environ,
    }
if __name__ == '__main__':
    asyncio.run(server.run_stdio_async())
