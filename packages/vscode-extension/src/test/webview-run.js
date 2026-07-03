/**
 * VSCode WebView E2E test — opens View Diagram, captures console output.
 * Run: node src/test/webview-e2e.js
 */
const { runTests } = require("@vscode/test-electron");
const path = require("path");

async function main() {
  const extDir = path.resolve(__dirname, "../../");
  const testFile = path.resolve(__dirname, "./webview-test.js");
  const workspace = path.resolve(__dirname, "../../../../Product");

  await runTests({
    extensionDevelopmentPath: extDir,
    extensionTestsPath: testFile,
    launchArgs: [
      workspace,
      "--disable-extensions",
      "--user-data-dir",
      "/tmp/vscode-webview-test",
    ],
    version: "stable",
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
