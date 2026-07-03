/**
 * Bundle ALL frontend code into a single JS file — no dynamic imports.
 * This is needed for VSCode WebView which blocks all external resources.
 * Usage: node bundle-all.js
 */
const esbuild = require("esbuild");
const fs = require("fs");
const path = require("path");

async function main() {
  // Bundle React app into single file
  const result = await esbuild.build({
    entryPoints: ["src/pages/_app.tsx"],
    bundle: true,
    write: false,
    format: "iife",
    globalName: "SpecEditor",
    platform: "browser",
    target: "es2020",
    minify: false,
    define: {
      "process.env.NODE_ENV": '"production"',
    },
    alias: {
      "@": "./src",
    },
    loader: {
      ".tsx": "tsx",
      ".ts": "ts",
      ".js": "js",
    },
    external: ["next/*", "react/jsx-runtime"],
  });

  const jsCode = result.outputFiles[0].text;

  // Create standalone HTML
  const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root{--bg-page:#0f0f1a;--bg-panel:#16213e;--accent:#4A90D9;--font:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg-page);color:#eee;font-family:var(--font);min-height:100vh}
  @keyframes spin{to{transform:rotate(360deg)}}
  .app{max-width:1400px;margin:0 auto;padding:16px 20px;display:flex;flex-direction:column}
  .panel{background:var(--bg-panel);border-radius:8px;border:1px solid #333}
  .error-banner{background:#3e1a1a;border:1px solid #f44336;border-radius:4px;padding:10px 14px;margin-bottom:12px;color:#faa;font-size:13px}
  .loading-state{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px}
  .spinner{width:24px;height:24px;border:2px solid #333;border-top:2px solid var(--accent);border-radius:50%;animation:spin 1s linear infinite}
  .tree{display:flex;flex-direction:column;max-height:100%;overflow:hidden}
  .tree-search{padding:8px 12px;border-bottom:1px solid #333}
  .tree-search input{width:100%;padding:6px 10px;background:#1a1a2e;border:1px solid #444;border-radius:4px;color:#eee;font-size:13px;outline:none}
  .tree-list{flex:1;overflow-y:auto;padding:4px 0}
  .aspect-group{border-bottom:1px solid #222}
  .aspect-header{display:flex;align-items:center;gap:6px;width:100%;padding:8px 12px;background:none;border:none;color:#ccc;font-size:13px;font-weight:600;cursor:pointer;text-align:left;font-family:inherit}
  .element-item{display:flex;align-items:center;gap:8px;width:100%;padding:5px 12px 5px 28px;background:none;border:none;color:#aaa;font-size:12px;cursor:pointer;text-align:left;font-family:monospace}
  .element-item.selected{background:rgba(74,144,217,0.15);color:#eee}
  .element-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
  .element-id{color:var(--accent);font-weight:600;min-width:70px}
  .element-title{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--font)}
  .header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #222}
  .header h1{font-size:24px;font-weight:700;color:var(--accent)}
  .tabs{display:flex;gap:4px;margin-bottom:12px}
  .tab{padding:8px 18px;background:none;border:1px solid #333;border-radius:6px 6px 0 0;color:#888;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;border-bottom:none}
  .tab.active{color:var(--accent);background:var(--bg-panel);border-color:var(--accent)}
  .split-view{display:grid;grid-template-columns:320px 1fr;gap:12px;height:calc(100vh - 240px)}
  .content{flex:1;overflow:hidden}
  .footer{display:flex;align-items:center;gap:8px;margin-top:12px;padding-top:10px;border-top:1px solid #222;font-size:11px;flex-wrap:wrap}
  .detail-header{margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #333}
  .detail-id{font-family:monospace;font-size:13px;font-weight:700;color:var(--accent)}
  .detail-status{font-size:11px;font-weight:600;text-transform:uppercase;padding:2px 6px;background:rgba(255,255,255,0.05);border-radius:3px}
  .detail-title{font-size:18px;font-weight:600;margin:0}
  .detail-meta{display:flex;gap:16px;margin-top:8px;flex-wrap:wrap;font-size:12px;color:#888}
  .detail-section{margin-top:16px;padding-top:12px;border-top:1px solid #2a2a4a}
  .detail-section-title{font-size:12px;font-weight:600;color:#888;text-transform:uppercase;margin:0 0 8px 0}
  .detail-content{font-size:14px;line-height:1.6;color:#ccc;white-space:pre-wrap}
  .detail-link{display:flex;align-items:center;gap:4px;padding:6px 10px;background:rgba(74,144,217,0.08);border:1px solid rgba(74,144,217,0.15);border-radius:4px;color:var(--accent);cursor:pointer;font-size:13px;text-align:left;font-family:inherit;width:fit-content}
  .diagram-container{min-height:200px;overflow:auto;display:flex;justify-content:center}
  .aspect-chips{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
  .chip{padding:6px 14px;background:var(--bg-panel);border:1px solid #333;border-radius:16px;color:#888;font-size:12px;cursor:pointer;font-family:inherit;text-transform:capitalize}
  .chip.active{background:rgba(74,144,217,0.15);border-color:var(--accent);color:var(--accent)}
  .validation-summary{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid}
  .issues-list{padding:8px;max-height:400px;overflow-y:auto}
  .issue-item{padding:10px 12px;margin-bottom:6px;background:rgba(255,255,255,0.03);border-radius:4px;border-left:3px solid}
  .metrics-grid{padding:16px;display:flex;flex-wrap:wrap;gap:10px}
  .metric-card{flex:1 1 calc(33% - 10px);min-width:100px;background:rgba(255,255,255,0.03);border-radius:6px;padding:12px;text-align:center}
  .metric-value{font-size:24px;font-weight:700}
  .metric-label{font-size:11px;color:#888;text-transform:uppercase}
  .breakdown{width:100%;margin-top:8px;padding:12px;background:rgba(255,255,255,0.02);border-radius:6px}
  .breakdown-row{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  .breakdown-label{font-size:11px;color:#aaa;min-width:100px}
  .breakdown-bar{flex:1;height:6px;background:#1a1a2e;border-radius:3px;overflow:hidden}
  .breakdown-fill{height:100%;background:var(--accent);border-radius:3px;transition:width 0.3s}
  .breakdown-count{font-size:11px;color:#666;min-width:30px;text-align:right}
</style>
</head>
<body>
  <div id="__next"></div>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script>
    // VSCode MCP bridge
    var v=acquireVsCodeApi();var id=0;var pend={};
    window.addEventListener('message',function(e){var m=e.data;if(m&&m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id]}});
    window.__vscode_mcp=function(method,params){return new Promise(function(resolve,reject){var cid=++id;pend[cid]=function(m){if(m.error)reject(new Error(m.error));else resolve(m.result.result||m.result)};v.postMessage({type:'mcp',body:{jsonrpc:'2.0',id:cid,method:method,params:params||{}}})})};
  </script>
  <script>${jsCode}</script>
</body>
</html>`;

  fs.writeFileSync("out/webview.html", html);
  console.log(`Bundled: out/webview.html (${Buffer.byteLength(html)} bytes)`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
