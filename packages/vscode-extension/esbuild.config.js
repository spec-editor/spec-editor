/**
 * esbuild bundler config for VSCode extension.
 *
 * Bundles the extension into a single minified file,
 * externalizes vscode (provided by the host), and
 * keeps @modelcontextprotocol/sdk inline.
 *
 * Usage: node esbuild.config.js
 */
const esbuild = require("esbuild");

const isWatch = process.argv.includes("--watch");
const isProduction = process.argv.includes("--production");

const config = {
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  external: ["vscode", "http", "child_process", "path", "fs"],
  format: "cjs",
  platform: "node",
  target: "node16",
  sourcemap: !isProduction,
  minify: isProduction,
  treeShaking: true,
  logLevel: "info",
};

async function main() {
  if (isWatch) {
    const ctx = await esbuild.context(config);
    await ctx.watch();
    console.log("👀 Watching for changes...");
  } else {
    await esbuild.build(config);
    console.log("✅ Build complete");
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
