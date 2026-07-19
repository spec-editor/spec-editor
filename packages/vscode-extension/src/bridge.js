/**
 * VSCode Webview Bridge — proxies MCP requests from webview to extension host.
 *
 * The frontend calls fetch("vscode://mcp", { method: "POST", body: JSON.stringify(rpc) }).
 * This bridge intercepts those calls and sends them via postMessage to the extension,
 * which forwards them to the MCP server over HTTP.
 *
 * Must be loaded BEFORE any other scripts in the webview HTML.
 */
(function () {
  "use strict";

  const vscode = acquireVsCodeApi();

  // Pending requests: id → { resolve, reject }
  const pending = {};
  let reqId = 0;

  // Listen for responses from the extension host
  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || msg.id === undefined) return;
    const p = pending[msg.id];
    if (!p) return;
    delete pending[msg.id];

    if (msg.result) {
      p.resolve(new Response(JSON.stringify(msg.result), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    } else if (msg.error) {
      p.reject(new Error(msg.error.message || "MCP request failed"));
    } else {
      p.resolve(new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    }
  });

  // Intercept fetch("vscode://mcp", ...)
  const origFetch = window.fetch;
  window.fetch = function (url, options) {
    if (typeof url === "string" && url.startsWith("vscode://mcp")) {
      return new Promise((resolve, reject) => {
        const rawId = "mcp-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
        pending[rawId] = { resolve, reject };

        let body;
        try {
          body = options && options.body ? JSON.parse(options.body) : {};
        } catch (e) {
          body = {};
        }
        // VSCode extension expects { type: "mcp", body: { method, params }, id }
        vscode.postMessage({
          type: "mcp",
          body: body,
          id: rawId,
        });

        // Timeout after 30s
        setTimeout(() => {
          if (pending[rawId]) {
            delete pending[rawId];
            reject(new Error("MCP request timed out"));
          }
        }, 30000);
      });
    }
    return origFetch.call(this, url, options);
  };
})();
