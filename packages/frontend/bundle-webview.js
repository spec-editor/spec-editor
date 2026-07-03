/**
 * Bundle frontend for VSCode WebView — all JS/CSS inlined.
 * Run after: cd packages/frontend && STATIC_EXPORT=1 npm run build
 * Usage: node bundle-webview.js
 */
const fs = require("fs");
const path = require("path");

const outDir = path.resolve(__dirname, "out");
const indexHtml = path.join(outDir, "index.html");

let html = fs.readFileSync(indexHtml, "utf-8");

// Inline all script chunks
const scriptRe = /<script src="([^"]+)"[^>]*><\/script>/g;
html = html.replace(scriptRe, (match, src) => {
  const filePath = path.join(outDir, src.replace(/^\.?\/?/, ""));
  if (fs.existsSync(filePath)) {
    return `<script>${fs.readFileSync(filePath, "utf-8")}</script>`;
  }
  return match;
});

// Inline CSS if any
const linkRe = /<link[^>]+href="([^"]+\.css)"[^>]*>/g;
html = html.replace(linkRe, (match, href) => {
  const filePath = path.join(outDir, href.replace(/^\.?\/?/, ""));
  if (fs.existsSync(filePath)) {
    return `<style>${fs.readFileSync(filePath, "utf-8")}</style>`;
  }
  return match;
});

// Write bundled HTML
const outFile = path.join(outDir, "webview.html");
fs.writeFileSync(outFile, html);
console.log(`Bundled: ${outFile} (${Buffer.byteLength(html)} bytes)`);
