import { randomUUID } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";

import {
  filesystemWriteInputSchema,
  filesystemWriteTool,
  findInputSchema,
  findProjectTool,
  gitChangedFiles,
  gitChangedFilesInputSchema,
  gitDiff,
  gitDiffInputSchema,
  gitLog,
  gitLogInputSchema,
  gitStatus,
  grepInputSchema,
  grepProjectTool,
  laptopTerminalRun,
  laptopTerminalRunInputSchema,
  listDirectoryTool,
  listInputSchema,
  logAppendInputSchema,
  logAppendTool,
  readFileTool,
  readInputSchema,
} from "./tools/index.js";
import { createWorkspaceGuard } from "./workspace/path-guard.js";
import { resolveHostConfinement } from "./execution/seam.js";
import { defaultExecutionProviders } from "./execution/providers/index.js";
import { withUnconfinedFallback, type ProviderRegistry } from "./execution/registry.js";

const workspaceRoot = process.env.ODESSY_WORKSPACE_ROOT || process.cwd();

/**
 * The host's execution targets, as a registry.
 *
 * Built once and cached: provider selection reads *host facts*, which discovery
 * gathers by probing (and on Windows, by running the confinement runner once), so it
 * must not be redone per call. The registry is what makes selection a fact about this
 * host rather than a guess.
 */
let executionRegistry: ProviderRegistry | undefined;

function executionProviders(): ProviderRegistry {
  executionRegistry ??= withUnconfinedFallback(defaultExecutionProviders());
  return executionRegistry;
}

type StructuredResult = Record<string, unknown>;

function resultPayload(result: object) {
  const structuredContent: StructuredResult = { ...result };
  return {
    content: [{ type: "text" as const, text: JSON.stringify(structuredContent) }],
    structuredContent,
  };
}

/**
 * Build the tool server.
 *
 * `registry` is injectable so a test can pin the provider set. Production uses the
 * host's real providers.
 *
 * The terminal tool resolves its confinement **per call** from the `mode` the authority
 * layer sent with that call, rather than from a single confinement fixed at startup —
 * because the mode is a property of the action, not of the host. The host does not
 * choose it; it selects a provider that satisfies it, and throws when none does.
 */
function buildServer(registry?: ProviderRegistry): McpServer {
  const guard = createWorkspaceGuard(workspaceRoot);
  const providers = registry ?? executionProviders();
  const server = new McpServer({
    name: "odessy-tool-server",
    version: "0.1.0",
  });

  server.registerTool(
    "filesystem.read",
    {
      title: "Read File",
      description: "Read a regular text file inside the Odessy workspace.",
      inputSchema: readInputSchema,
    },
    async (input) => resultPayload(await readFileTool(guard, input)),
  );

  server.registerTool(
    "filesystem.list",
    {
      title: "List Directory",
      description: "List directory entries inside the Odessy workspace.",
      inputSchema: listInputSchema,
    },
    async (input) => resultPayload(await listDirectoryTool(guard, input)),
  );

  server.registerTool(
    "project.find",
    {
      title: "Find Files",
      description: "Find file names inside the Odessy workspace.",
      inputSchema: findInputSchema,
    },
    async (input) => resultPayload(await findProjectTool(guard, input)),
  );

  server.registerTool(
    "project.grep",
    {
      title: "Grep Project",
      description: "Search file contents inside the Odessy workspace.",
      inputSchema: grepInputSchema,
    },
    async (input) => resultPayload(await grepProjectTool(guard, input)),
  );

  server.registerTool(
    "git.status",
    {
      title: "Git Status",
      description: "Return fixed-argv git status output.",
      inputSchema: {},
    },
    async () => resultPayload(await gitStatus(guard)),
  );

  server.registerTool(
    "git.diff",
    {
      title: "Git Diff",
      description: "Return working or cached git diff using fixed argv.",
      inputSchema: gitDiffInputSchema,
    },
    async (input) => resultPayload(await gitDiff(guard, input)),
  );

  server.registerTool(
    "git.changed_files",
    {
      title: "Git Changed Files",
      description: "Return changed files using fixed git diff argv.",
      inputSchema: gitChangedFilesInputSchema,
    },
    async (input) => resultPayload(await gitChangedFiles(guard, input)),
  );

  server.registerTool(
    "git.log",
    {
      title: "Git Log",
      description: "Return recent commit summaries using fixed argv.",
      inputSchema: gitLogInputSchema,
    },
    async (input) => resultPayload(await gitLog(guard, input)),
  );

  server.registerTool(
    "laptop.terminal.run",
    {
      title: "Laptop Terminal Run",
      description: "Run a laptop-side command in the Odessy workspace.",
      inputSchema: laptopTerminalRunInputSchema,
    },
    async (input) => {
      // No requirement means the caller expressed none: run unconfined and SAY so. A
      // requirement means the authority layer decided it, so this side selects a
      // provider for it and fails closed when none satisfies it. The requirement is
      // never inferred from the mode -- see `resolveHostConfinement`.
      const confinement = resolveHostConfinement(input, guard.root, providers);
      return resultPayload(await laptopTerminalRun(guard, input, confinement));
    },
  );

  server.registerTool(
    "filesystem.write",
    {
      title: "Write File",
      description: "Write content to a file inside the Odessy workspace.",
      inputSchema: filesystemWriteInputSchema,
    },
    async (input) => resultPayload(await filesystemWriteTool(guard, input)),
  );

  server.registerTool(
    "log.append",
    {
      title: "Append Log",
      description: "Append a line to the internal Odessy log at .odessy/logs/odessy.log.",
      inputSchema: logAppendInputSchema,
    },
    async (input) => resultPayload(await logAppendTool(guard, input)),
  );

  return server;
}

async function readJsonBody(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  if (chunks.length === 0) {
    return undefined;
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

async function runHttpServer(): Promise<void> {
  const host = process.env.ODESSY_MCP_HOST || "127.0.0.1";
  const port = Number.parseInt(process.env.ODESSY_MCP_PORT || "8765", 10);
  const transports = new Map<string, StreamableHTTPServerTransport>();

  const httpServer = createServer(async (request: IncomingMessage, response: ServerResponse) => {
    try {
      const url = new URL(request.url || "/", `http://${request.headers.host || host}`);
      if (url.pathname !== "/mcp") {
        response.writeHead(404).end("Not found");
        return;
      }

      const body = request.method === "POST" ? await readJsonBody(request) : undefined;
      const sessionId = request.headers["mcp-session-id"];
      const sessionKey = Array.isArray(sessionId) ? sessionId[0] : sessionId;
      let transport = sessionKey ? transports.get(sessionKey) : undefined;

      if (!transport && request.method === "POST" && isInitializeRequest(body as any)) {
        let sessionTransport: StreamableHTTPServerTransport;
        sessionTransport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (newSessionId: string) => {
            transports.set(newSessionId, sessionTransport);
          },
        });
        sessionTransport.onclose = () => {
          if (sessionTransport.sessionId) {
            transports.delete(sessionTransport.sessionId);
          }
        };
        await buildServer().connect(sessionTransport);
        transport = sessionTransport;
      }

      if (!transport) {
        response.writeHead(400).end("Missing or invalid MCP session");
        return;
      }

      await transport.handleRequest(request, response, body);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!response.headersSent) {
        response.writeHead(500);
      }
      response.end(message);
    }
  });

  await new Promise<void>((resolve) => {
    httpServer.listen(port, host, resolve);
  });
  process.stderr.write(`Odessy MCP HTTP server listening on http://${host}:${port}/mcp\n`);
}

if (process.argv.includes("--http") || process.env.ODESSY_MCP_TRANSPORT === "http") {
  await runHttpServer();
} else {
  await buildServer().connect(new StdioServerTransport());
}
