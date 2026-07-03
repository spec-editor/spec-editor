/**
 * VSCode Extension E2E test runner.
 *
 * Launches VSCode with the extension, runs Mocha tests.
 * Requires: VSCode installed (or downloaded automatically).
 *
 * Usage: node src/test/runTests.js
 */
const { runTests } = require("@vscode/test-electron");
const path = require("path");
const { execSync } = require("child_process");
const http = require("http");

const MCP_PORT = 8088;

/** Kill any process on MCP_PORT and stop Docker if it owns the port. */
function ensurePortFree() {
  try {
    // 1. Kill any local Python MCP server on the port
    execSync(`lsof -ti :${MCP_PORT} | xargs kill -9 2>/dev/null`, {
      stdio: "ignore",
    });
    console.log(`Cleaned port ${MCP_PORT}`);
  } catch {
    // nothing on port — fine
  }

  // 2. Stop Docker MCP container if running (docker-compose from project root)
  try {
    const projectRoot = path.resolve(__dirname, "../../../../");
    execSync("docker compose stop mcp 2>/dev/null || true", {
      cwd: projectRoot,
      stdio: "ignore",
      timeout: 5000,
    });
    console.log("Stopped Docker MCP container (if any)");
  } catch {
    // Docker not running or no container — fine
  }

  // 3. Wait for port to be actually free
  return new Promise((resolve) => {
    let attempts = 0;
    const check = () => {
      const req = http.request(
        {
          hostname: "127.0.0.1",
          port: MCP_PORT,
          path: "/",
          method: "GET",
          timeout: 500,
        },
        () => {
          /* still listening — wait */ attempts++;
          if (attempts < 10) setTimeout(check, 500);
          else resolve();
        },
      );
      req.on("error", () => resolve()); // ECONNREFUSED = port free
      req.end();
    };
    check();
  });
}

async function main() {
  try {
    // Pre-test cleanup: ensure MCP port is free (kill local + Docker)
    console.log("Cleaning up before tests...");
    await ensurePortFree();
    // Path to the extension
    const extensionDevelopmentPath = path.resolve(__dirname, "../../");

    // Path to test files (plain JS — no compilation needed)
    const extensionTestsPath = path.resolve(__dirname, "./e2e.test.js");

    // Test workspace (a spec-editor project)
    const testWorkspace = path.resolve(__dirname, "../../../../Product");

    console.log("Extension path:", extensionDevelopmentPath);
    console.log("Test path:", extensionTestsPath);
    console.log("Workspace:", testWorkspace);

    // Download VSCode, unzip, launch, run tests
    await runTests({
      extensionDevelopmentPath,
      extensionTestsPath,
      launchArgs: [
        testWorkspace,
        "--user-data-dir",
        "/tmp/vscode-test-userdata-" + Date.now(),
      ],
      // Use stable VSCode, download if needed
      version: "stable",
    });
  } catch (err) {
    console.error("E2E test failed:", err);
    process.exit(1);
  }

  // Check latest log file for ERROR entries.
  // The extension writes /tmp/spec-editor-<timestamp>.log
  // and creates a symlink /tmp/spec-editor-latest.log -> that file.
  try {
    const fs = require("fs");
    const candidates = [
      "/tmp/spec-editor-test-latest.log",
      "/tmp/spec-editor-latest.log",
    ];
    let checked = false;
    for (const logPath of candidates) {
      if (!fs.existsSync(logPath)) continue;
      const log = fs.readFileSync(logPath, "utf-8");
      const errors = log.split("\n").filter((l) => l.includes(" ERROR "));
      if (errors.length > 0) {
        console.log("\n=== LOG ERRORS FOUND in " + logPath + " ===");
        errors.forEach((e) => console.log(e));
        process.exit(1);
      }
      console.log("\nLog check (" + logPath + "): no ERROR entries");
      checked = true;
      break;
    }
    if (!checked) {
      console.log("No log file found — skipping log check");
    }
  } catch {
    // log file may not exist
  }
}

main();
