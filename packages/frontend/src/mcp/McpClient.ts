/**
 * MCP JSON-RPC client over HTTP.
 *
 * Communicates with the spec-editor MCP server (Python backend).
 * Used by the frontend to call MCP tools directly — replaces REST API.
 *
 * Usage:
 *   const mcp = new McpClient("http://127.0.0.1:5123/mcp");
 *   const result = await mcp.callTool("read_element", { element_id: "MOD-001" });
 */

export interface McpResponse {
  content: Array<{ type: string; text: string }>;
  isError?: boolean;
}

export interface ServerInfo {
  name: string;
  version: string;
  editor: string;
}

export interface ToolDef {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export class McpClient {
  private serverUrl: string;
  private requestId = 0;
  private projectPath: string;

  constructor(serverUrl: string, projectPath?: string) {
    this.serverUrl = serverUrl;
    this.projectPath = projectPath || "";
  }

  /** Set the project path for multi-project MCP calls. */
  setProjectPath(path: string): void {
    this.projectPath = path;
  }

  /** Initialize MCP session. Returns server info. */
  async initialize(): Promise<ServerInfo> {
    const result = await this.sendRequest<{ serverInfo: ServerInfo }>(
      "initialize",
      {},
    );
    return result.serverInfo;
  }

  /** List all available tools. */
  async listTools(): Promise<ToolDef[]> {
    const result = await this.sendRequest<{ tools: ToolDef[] }>(
      "tools/list",
      {},
    );
    return result.tools;
  }

  /** Call an MCP tool by name with arguments.
   *  Automatically injects project_path for stateful tools. */
  async callTool(
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<unknown> {
    // Multi-project: auto-inject project_path for stateful tools
    const STATELESS = new Set(["list_projects", "get_project_info", "analyze_image", "list_diagram_types"]);
    if (this.projectPath && !STATELESS.has(toolName) && !args.project_path) {
      args = { project_path: this.projectPath, ...args };
    }
    const result = await this.sendRequest(
      "tools/call",
      { name: toolName, arguments: args },
      true,
    );
    return result;
  }

  /**
   * Call a tool and parse the result as JSON.
   * MCP wraps tool results in content[0].text as JSON string.
   */
  async callToolJson<T = unknown>(
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<T> {
    const raw = (await this.callTool(toolName, args)) as McpResponse;
    if (raw.isError) {
      throw new Error(raw.content[0]?.text ?? "MCP error");
    }
    return JSON.parse(raw.content[0].text) as T;
  }

  // ── VSCode WebView transport ───────────────────────────────────────

  private _vscodeApi: any = null;

  private getVscodeApi(): any {
    if (!this._vscodeApi && typeof window !== "undefined") {
      const win = window as any;
      if (typeof win.acquireVsCodeApi === "function") {
        this._vscodeApi = win.acquireVsCodeApi();
      }
    }
    return this._vscodeApi;
  }

  private sendViaVscode<T = unknown>(
    body: Record<string, unknown>,
  ): Promise<T> {
    const vscodeMcp = (window as any).__vscode_mcp;
    if (!vscodeMcp) {
      return Promise.reject(new Error("VSCode MCP bridge not available"));
    }
    return vscodeMcp(body.method, body.params);
  }

  // ── Internal ─────────────────────────────────────────────────────────

  private async sendRequest<T = unknown>(
    method: string,
    params: Record<string, unknown>,
    isToolCall = false,
  ): Promise<T> {
    const body = {
      jsonrpc: "2.0",
      id: ++this.requestId,
      method,
      params,
    };

    console.log(`[spec-editor] sendRequest ${method} url=${this.serverUrl}`);

    // VSCode WebView transport
    if (this.serverUrl.startsWith("vscode://")) {
      console.log(`[spec-editor] → vscode bridge`);
      const result = await this.sendViaVscode<T>(body);
      console.log(`[spec-editor] ← vscode result:`, result);
      return result;
    }

    const response = await fetch(this.serverUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      throw new Error(`MCP HTTP ${response.status}: ${response.statusText}`);
    }

    const json = await response.json();

    if (json.error) {
      throw new Error(
        `MCP error: ${json.error.message ?? JSON.stringify(json.error)}`,
      );
    }

    return json.result as T;
  }
}

/**
 * Auto-detect MCP server URL based on adapter.
 *
 * In VSCode Webview: uses window.__SPEC_EDITOR_MCP_PORT__
 * In JetBrains JCEF: uses window.__SPEC_EDITOR_MCP_PORT__
 * Behind nginx (Docker): uses relative /api/mcp
 * In standalone browser: uses localhost:8088 (default)
 */
const MCP_PORT = 8088; // synced with src/mcp/server.py:_DEFAULT_MCP_PORT

export function getMcpUrl(): string {
  const result = (() => {
    if (typeof window !== "undefined") {
      const win = window as unknown as Record<string, unknown>;
      if (
        window.location.protocol === "vscode-webview:" ||
        typeof (win as any).acquireVsCodeApi !== "undefined"
      ) {
        return "vscode://mcp";
      }
      if (win.__SPEC_EDITOR_MCP_PORT__) {
        return `http://127.0.0.1:${win.__SPEC_EDITOR_MCP_PORT__}/mcp`;
      }
      if (win.__SPEC_EDITOR_NGINX_PROXY__ || isBehindNginx()) {
        return "/api/mcp";
      }
    }
    return `http://127.0.0.1:${MCP_PORT}/mcp`;
  })();
  console.log("[spec-editor] getMcpUrl =>", result);
  return result;
}

function isBehindNginx(): boolean {
  if (typeof window !== "undefined") {
    const port = window.location.port;
    return port === "80" || port === "" || port === "3000";
  }
  return false;
}

/**
 * Auto-detect project path for multi-project MCP.
 *
 * Priority:
 * 1. window.__SPEC_EDITOR_PROJECT_PATH__ (set by VSCode/JetBrains extension)
 * 2. window.__vscode_workspace_path (VS Code webview context)
 * 3. Empty string (will cause MCP to return error if project_path is required)
 */
export function getProjectPath(): string {
  if (typeof window !== "undefined") {
    const win = window as unknown as Record<string, unknown>;
    if (typeof win.__SPEC_EDITOR_PROJECT_PATH__ === "string") {
      return win.__SPEC_EDITOR_PROJECT_PATH__ as string;
    }
    if (typeof (win as any).__vscode_workspace_path === "string") {
      return (win as any).__vscode_workspace_path;
    }
  }
  return "";
}

/**
 * SSE endpoint for real-time events.
 */
export function getSseUrl(): string {
  if (typeof window !== "undefined") {
    const win = window as unknown as Record<string, unknown>;
    if (win.__SPEC_EDITOR_MCP_PORT__) {
      return `http://127.0.0.1:${win.__SPEC_EDITOR_MCP_PORT__}/events`;
    }
    if (win.__SPEC_EDITOR_NGINX_PROXY__ || isBehindNginx()) {
      return "/api/events";
    }
  }
  return `http://127.0.0.1:${MCP_PORT}/events`;
}
