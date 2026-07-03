"use strict";
/**
 * VSCode Extension E2E tests.
 *
 * Runs inside VSCode via @vscode/test-electron.
 * Uses Mocha + VSCode API (CommonJS — VSCode requires require(), not import).
 */
// eslint-disable-next-line @typescript-eslint/no-require-imports
const assert = require("assert");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const vscode = require("vscode");
// =============================================================================
// Helpers
// =============================================================================
async function waitForActivation() {
    const ext = vscode.extensions.getExtension("spec-editor.spec-editor-vscode");
    if (!ext)
        throw new Error("Extension not found");
    if (!ext.isActive)
        await ext.activate();
    return ext;
}
async function waitForMcpServer(port = 8088, timeoutMs = 30000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        try {
            const response = await fetch(`http://127.0.0.1:${port}/mcp`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    jsonrpc: "2.0",
                    id: 1,
                    method: "initialize",
                    params: {},
                }),
            });
            if (response.ok) {
                const data = (await response.json());
                if (data.result?.serverInfo)
                    return;
            }
        }
        catch {
            // Not ready
        }
        await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error(`MCP server not ready on ${port} after ${timeoutMs}ms`);
}
// =============================================================================
// Tests
// =============================================================================
suite("Spec Editor — VSCode Extension E2E", () => {
    test("Extension is found", () => {
        const ext = vscode.extensions.getExtension("spec-editor.spec-editor-vscode");
        assert.ok(ext, "Extension should be registered");
    });
    test("Extension activates", async () => {
        const ext = await waitForActivation();
        assert.ok(ext.isActive, "Extension should be active");
    });
    test("Commands are registered", async () => {
        const commands = await vscode.commands.getCommands(true);
        const specCommands = commands.filter((c) => c.startsWith("specEditor."));
        console.log(`Found ${specCommands.length} specEditor commands:`, specCommands);
        assert.ok(specCommands.length >= 4, `Expected >=4 commands, got ${specCommands.length}`);
        assert.ok(specCommands.includes("specEditor.open"), "Missing specEditor.open");
        assert.ok(specCommands.includes("specEditor.newProject"), "Missing specEditor.newProject");
        assert.ok(specCommands.includes("specEditor.viewDiagram"), "Missing specEditor.viewDiagram");
        assert.ok(specCommands.includes("specEditor.validate"), "Missing specEditor.validate");
    });
    test("Configuration has defaults", () => {
        const config = vscode.workspace.getConfiguration("specEditor");
        assert.ok(typeof config.get("mcpPort") === "number", "mcpPort should be number");
        assert.ok(typeof config.get("pythonPath") === "string", "pythonPath should be string");
        assert.ok(typeof config.get("autoStartMcp") === "boolean", "autoStartMcp should be boolean");
    });
    test("MCP server starts on port 8088", async function () {
        this.timeout(60000);
        await waitForMcpServer(8088, 45000);
        const response = await fetch("http://127.0.0.1:8088/mcp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                jsonrpc: "2.0",
                id: 1,
                method: "initialize",
                params: {},
            }),
        });
        const data = (await response.json());
        assert.ok(data.result?.serverInfo, "Should have serverInfo");
        assert.strictEqual(data.result.serverInfo.name, "spec-editor-mcp");
    });
    test("MCP tools/list returns tools", async () => {
        const response = await fetch("http://127.0.0.1:8088/mcp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                jsonrpc: "2.0",
                id: 2,
                method: "tools/list",
                params: {},
            }),
        });
        const data = (await response.json());
        const tools = data.result?.tools || [];
        assert.ok(tools.length >= 20, `Expected >=20 tools, got ${tools.length}`);
        const names = tools.map((t) => t.name);
        assert.ok(names.includes("read_element"), "Missing read_element");
        assert.ok(names.includes("list_all_elements"), "Missing list_all_elements");
        assert.ok(names.includes("generate_diagram"), "Missing generate_diagram");
        assert.ok(names.includes("run_validate"), "Missing run_validate");
        assert.ok(names.includes("run_metrics"), "Missing run_metrics");
    });
    test("MCP list_all_elements and read_element work", async () => {
        const listResp = await fetch("http://127.0.0.1:8088/mcp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                jsonrpc: "2.0",
                id: 3,
                method: "tools/call",
                params: { name: "list_all_elements", arguments: {} },
            }),
        });
        const listData = (await listResp.json());
        const elements = JSON.parse(listData.result.content[0].text).elements;
        console.log(`Elements: ${elements?.length || 0}`);
        if (elements && elements.length > 0) {
            const elId = elements[0].id;
            const readResp = await fetch("http://127.0.0.1:8088/mcp", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    jsonrpc: "2.0",
                    id: 4,
                    method: "tools/call",
                    params: { name: "read_element", arguments: { element_id: elId } },
                }),
            });
            const readData = (await readResp.json());
            const detail = JSON.parse(readData.result.content[0].text);
            assert.strictEqual(detail.id, elId);
        }
    });
});
//# sourceMappingURL=e2e.test.js.map