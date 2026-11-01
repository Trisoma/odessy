"""Collaboration tools mounted only by an explicitly configured composition root."""
import asyncio
from mcp.server import MCPServer


def build_collaboration_server(service, jobs=None, runs=None):
    server = MCPServer("odessy-collaboration")

    @server.tool(name="model.consult")
    async def consult(participant: str, question: str, context: str = "", reason: str = "") -> dict[str, object]:
        """Get attributed advice from a configured participant; do not execute its reply."""
        worker = asyncio.create_task(asyncio.to_thread(service.consult, participant=participant,
            question=question, context=context, reason=reason))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()
            raise

    if jobs is not None:
        @server.tool(name="agent.delegate")
        async def delegate(participant: str, task: str, mode: str = "advice", workspace: str = "") -> dict[str, object]:
            # This server is an ExecutionTarget, and OdessyAgent has already asked
            # its confirmation channel. The model cannot supply `approved`.
            return await jobs.submit(participant, task, mode=mode, workspace=workspace or None, approved=True)

        @server.tool(name="agent.job_status")
        async def status(job_id: str) -> dict[str, object]:
            return await jobs.refresh(job_id)

        @server.tool(name="agent.cancel")
        async def cancel(job_id: str) -> dict[str, object]:
            return await jobs.cancel(job_id)

    if runs is not None:
        @server.tool(name="agent.run")
        async def run(flow: str, from_participant: str, from_task: str, to_participant: str, to_task: str,
                      workspace: str = "") -> dict[str, object]:
            # Confirmed by OdessyAgent, like agent.delegate.
            return await runs.start(flow, from_participant, from_task, to_participant, to_task,
                                    workspace=workspace or None, approved=True)

        @server.tool(name="agent.run_continue")
        async def run_continue(run_id: str = "", artifact_sha256: str = "") -> dict[str, object]:
            return await runs.continue_run(run_id or None, artifact_sha256 or None, approved=True)

        @server.tool(name="agent.run_status")
        async def run_status(run_id: str = "") -> dict[str, object]:
            return runs.get(run_id or None)

        @server.tool(name="agent.run_cancel")
        async def run_cancel(run_id: str = "") -> dict[str, object]:
            return await runs.cancel(run_id or None)

    return server


def authorized_remote_targets(targets):
    """Use only hosts already admitted as execution targets; never discover trust."""
    from mcp import Client
    from odessy.runtime.agent_loop import ExecutionTarget
    from odessy.tools.external_tools import NamespacedServer
    from odessy.tools.lan_executor import OptionalExecutionServer

    result = {}
    for item in targets:
        if not isinstance(item, ExecutionTarget):
            continue
        server = item.server
        if isinstance(server, OptionalExecutionServer) and server.local:
            continue
        original = server.server if isinstance(server, OptionalExecutionServer) else server
        if isinstance(original, NamespacedServer):
            name = original.config.name
        elif isinstance(original, str):
            name = "laptop"
        else:
            continue

        async def dispatch(action, payload, server=server):
            name = {"start": "agent.delegate", "status": "agent.job_status", "cancel": "agent.cancel",
                    "consult": "model.consult"}[action]
            if action == "start":
                payload = {key: value for key, value in payload.items() if key in {"participant", "task", "mode", "workspace"}}
            if isinstance(server, (NamespacedServer, OptionalExecutionServer)):
                response = await server.call_tool(name, payload)
            else:
                async with Client(server) as client:
                    response = await client.call_tool(name, payload)
            if getattr(response, "is_error", getattr(response, "isError", False)):
                raise RuntimeError("Remote coding host rejected the request")
            data = getattr(response, "structured_content", getattr(response, "structuredContent", None))
            if isinstance(data, dict):
                return data
            raise RuntimeError("Remote coding host returned no structured job result")

        result[name] = dispatch
    return result
