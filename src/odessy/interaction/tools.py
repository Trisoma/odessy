"""The human information tool uses the session's existing interaction broker."""
from mcp.server import MCPServer


def build_interaction_server(broker_getter):
    server = MCPServer("odessy-human-interaction")

    @server.tool(name="user.ask")
    async def ask(question: str, choices: list[str] | None = None,
                  allow_free_text: bool = True) -> dict[str, object]:
        return await broker_getter().ask(question, choices=choices,
                                         allow_free_text=allow_free_text)

    return server
