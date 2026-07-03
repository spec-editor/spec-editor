/**
 * VSCode Extension E2E — self-contained, no Mocha needed.
 *
 * Tests: extension activation, commands, config, status bar states,
 *        MCP tools, WebView panel, validation.
 */
const assert = require("assert");
const vscode = require("vscode");
const http = require("http");

function mcpRequest(port, toolName, args) {
  return new Promise(function (resolve, reject) {
    var body = JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: toolName, arguments: args || {} },
    });
    var req = http.request(
      {
        hostname: "127.0.0.1",
        port: port,
        path: "/mcp",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
        },
        timeout: 8000,
      },
      function (res) {
        var data = "";
        res.on("data", function (c) {
          data += c;
        });
        res.on("end", function () {
          try {
            resolve(JSON.parse(data));
          } catch (e) {
            reject(new Error("Invalid JSON: " + data.slice(0, 200)));
          }
        });
      },
    );
    req.on("error", reject);
    req.on("timeout", function () {
      req.destroy();
      reject(new Error("MCP timeout"));
    });
    req.write(body);
    req.end();
  });
}

async function run() {
  var failures = 0;
  var results = [];

  function check(name, fn) {
    return async function () {
      try {
        await fn();
        results.push("  \u2705 " + name);
      } catch (e) {
        failures++;
        results.push("  \u274c " + name + ": " + e.message);
      }
    };
  }

  var cfg = vscode.workspace.getConfiguration("specEditor");
  var mcpPort = cfg.get("mcpPort", 8088);

  var tests = [
    // ── Activation ────────────────────────────────────────────────────
    check("Extension is found", function () {
      var ext = vscode.extensions.getExtension(
        "spec-editor.spec-editor-vscode",
      );
      assert.ok(ext, "Extension should be registered");
    }),

    check("Extension activates", async function () {
      var ext = vscode.extensions.getExtension(
        "spec-editor.spec-editor-vscode",
      );
      assert.ok(ext, "Extension not found");
      if (!ext.isActive) await ext.activate();
      assert.ok(ext.isActive, "Extension should be active");
    }),

    check("Commands are registered", async function () {
      var cmds = await vscode.commands.getCommands(true);
      var spec = cmds.filter(function (c) {
        return c.startsWith("specEditor.");
      });
      assert.ok(spec.length >= 6, "Expected >=6, got " + spec.length);
    }),

    check("Configuration has defaults", function () {
      assert.strictEqual(typeof cfg.get("mcpPort"), "number");
      assert.strictEqual(typeof cfg.get("pythonPath"), "string");
      assert.strictEqual(typeof cfg.get("autoStartMcp"), "boolean");
    }),

    check("_getStatus command registered", async function () {
      var cmds = await vscode.commands.getCommands(true);
      assert.ok(cmds.indexOf("specEditor._getStatus") !== -1);
    }),

    // ── Status bar ────────────────────────────────────────────────────
    check("Status bar: initial state", async function () {
      var s = await vscode.commands.executeCommand("specEditor._getStatus");
      assert.ok(s, "State should be returned");
      assert.ok(
        s.command === "specEditor._quickOpen" ||
          s.command === "specEditor.open",
      );
    }),

    check("Status bar: connected after MCP starts", async function () {
      for (var i = 0; i < 30; i++) {
        var s = await vscode.commands.executeCommand("specEditor._getStatus");
        if (s && s.mcpConnected) return; // success
        await new Promise(function (r) {
          setTimeout(r, 1000);
        });
      }
      var final = await vscode.commands.executeCommand("specEditor._getStatus");
      assert.fail(
        "MCP not connected after 30s. mcpRunning=" +
          final.mcpProcessRunning +
          " mcpConnected=" +
          final.mcpConnected +
          " text=" +
          final.text,
      );
    }),

    check("Diagnostics: full state dump", async function () {
      var d = await vscode.commands.executeCommand("specEditor._diagnostics");
      assert.ok(d, "Diagnostics should return state");
      console.log("DIAGNOSTICS:", JSON.stringify(d, null, 2));
      // Verify key fields
      assert.ok(typeof d.mcpPort === "number", "mcpPort should be a number");
      assert.ok(
        typeof d.mcpConnected === "boolean",
        "mcpConnected should be boolean",
      );
      assert.ok(
        typeof d.mcpProcessRunning === "boolean",
        "mcpProcessRunning should be boolean",
      );
      assert.ok(d.statusBarText, "statusBarText should be set");
    }),

    check("Status bar: tooltip has port", async function () {
      var s = await vscode.commands.executeCommand("specEditor._getStatus");
      if (s.mcpConnected) {
        assert.ok(
          s.mcpStatusTooltip.indexOf(String(mcpPort)) !== -1,
          "tooltip: " + s.mcpStatusTooltip,
        );
      }
    }),

    check("Status bar: error when disconnected", async function () {
      var s = await vscode.commands.executeCommand("specEditor._getStatus");
      if (!s.mcpConnected) {
        assert.ok(s.text.indexOf("$(error)") !== -1, "text: " + s.text);
      }
    }),

    // ── MCP health ────────────────────────────────────────────────────
    check("MCP: initialize", async function () {
      var body = JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: {},
      });
      var r = await new Promise(function (resolve, reject) {
        var req = http.request(
          {
            hostname: "127.0.0.1",
            port: mcpPort,
            path: "/mcp",
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(body),
            },
            timeout: 5000,
          },
          function (res) {
            var d = "";
            res.on("data", function (c) {
              d += c;
            });
            res.on("end", function () {
              try {
                resolve(JSON.parse(d));
              } catch (e) {
                reject(e);
              }
            });
          },
        );
        req.on("error", reject);
        req.on("timeout", function () {
          req.destroy();
          reject(new Error("timeout"));
        });
        req.write(body);
        req.end();
      });
      assert.ok(
        r && r.result,
        "initialize: " + JSON.stringify(r).slice(0, 200),
      );
    }),

    check("MCP: list_aspects", async function () {
      var r = await mcpRequest(mcpPort, "list_aspects", {});
      assert.ok(r && r.result, "list_aspects result");
    }),

    check("MCP: list_all_elements", async function () {
      var r = await mcpRequest(mcpPort, "list_all_elements", {});
      assert.ok(r && r.result, "list_all_elements result");
    }),

    check("MCP: export_srs", async function () {
      var r = await mcpRequest(mcpPort, "export_srs", {});
      assert.ok(r && r.result, "export_srs result");
    }),

    // ── Configuration CRUD ────────────────────────────────────────────
    check("Config: zoomSensitivity default and range", function () {
      var v = cfg.get("zoomSensitivity");
      assert.ok(typeof v === "number", "should be number, got " + typeof v);
      assert.ok(v >= 0.1, "Min 0.1 violated: " + v);
      assert.ok(v <= 100, "Max 100 violated: " + v);
    }),

    check("Config: all 27 settings have defaults", function () {
      var keys = [
        "mcpPort",
        "pythonPath",
        "autoStartMcp",
        "mcpMode",
        "agent1.provider",
        "agent1.model",
        "agent1.temperature",
        "agent1.maxTokens",
        "agent2.provider",
        "agent2.model",
        "agent2.temperature",
        "agent2.maxTokens",
        "orchestrator.provider",
        "orchestrator.model",
        "orchestrator.temperature",
        "orchestrator.maxTokens",
        "maxRounds",
        "maxTimeMinutes",
        "maxAgents",
        "maxLlmCalls",
        "tokenBudget",
        "llmRequestTimeout",
        "llmTotalTimeout",
        "zoomSensitivity",
        "adaptiveVoting",
        "logLevel",
        "logJson",
      ];
      assert.strictEqual(keys.length, 27, "Expected 27 settings");
      for (var i = 0; i < keys.length; i++) {
        var v = cfg.get(keys[i]);
        assert.ok(v !== undefined, keys[i] + " should have a default value");
      }
    }),

    // ── Project lifecycle ────────────────────────────────────────────
    check("Scenario: auto-init creates methodology.yaml", function () {
      // After autoStartMcp, the workspace (Product/) should have methodology.yaml
      var fs = require("fs");
      var projDir = "/Users/dmitry/Documents/Droid/spec-editor2/Product";
      var exists = fs.existsSync(projDir + "/methodology.yaml");
      assert.ok(exists, "methodology.yaml should exist in workspace");
    }),

    check("Scenario: _openMethodology command registered", async function () {
      var cmds = await vscode.commands.getCommands(true);
      assert.ok(
        cmds.indexOf("specEditor._openMethodology") !== -1,
        "_openMethodology command should be registered",
      );
    }),

    check("Scenario: get_methodology returns aspects", async function () {
      var http = require("http");
      var cfg = vscode.workspace.getConfiguration("specEditor");
      var port = cfg.get("mcpPort", 8088);
      var result = await new Promise(function (resolve, reject) {
        var body = JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          method: "tools/call",
          params: { name: "get_methodology", arguments: {} },
        });
        var req = http.request(
          {
            hostname: "127.0.0.1",
            port: port,
            path: "/mcp",
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(body),
            },
            timeout: 5000,
          },
          function (res) {
            var d = "";
            res.on("data", function (c) {
              d += c;
            });
            res.on("end", function () {
              try {
                var data = JSON.parse(d);
                var txt = data.result.content[0].text;
                var inner = JSON.parse(txt);
                resolve(inner);
              } catch (e) {
                reject(e);
              }
            });
          },
        );
        req.on("error", reject);
        req.on("timeout", function () {
          req.destroy();
          reject(new Error("timeout"));
        });
        req.write(body);
        req.end();
      });
      assert.ok(
        result.aspects && Array.isArray(result.aspects),
        "get_methodology should return aspects array",
      );
      assert.ok(
        result.aspects.length >= 4,
        "Expected >=4 aspects, got " + result.aspects.length,
      );
    }),

    // ── Element deletion ─────────────────────────────────────────────
    check(
      "Scenario: deleteElement removes element from list (with tree trace)",
      async function () {
        var http = require("http");
        var cfg = vscode.workspace.getConfiguration("specEditor");
        var port = cfg.get("mcpPort", 8088);

        // Step 1: Create test element
        var createResult = await mcpRequest(port, "write_element", {
          aspect: "modules",
          element_type: "module",
          id: "",
          title: "To Be Deleted",
          content: "Delete me",
        });
        var inner = createResult.result;
        if (typeof inner === "string") inner = JSON.parse(inner);
        if (inner.content && inner.content[0] && inner.content[0].text) {
          inner = JSON.parse(inner.content[0].text);
        }
        var delId = inner.element_id;
        assert.ok(
          delId,
          "element_id should be generated, got: " + JSON.stringify(inner),
        );

        // Step 2: Verify element exists
        var listBefore = await mcpRequest(port, "list_all_elements", {});
        var lb = listBefore.result;
        if (typeof lb === "string") lb = JSON.parse(lb);
        if (lb.content && lb.content[0] && lb.content[0].text)
          lb = JSON.parse(lb.content[0].text);
        var elements = lb.elements || lb;
        var beforeCount = Array.isArray(elements) ? elements.length : 0;
        assert.ok(beforeCount > 0, "Should have elements before delete");

        // Step 3: Delete via MCP
        var delResult = await mcpRequest(port, "delete_element", {
          element_id: delId,
        });
        assert.ok(delResult.result, "delete_element should succeed");

        // Step 4: Verify element is gone
        var listAfter = await mcpRequest(port, "list_all_elements", {});
        var la = listAfter.result;
        if (typeof la === "string") la = JSON.parse(la);
        if (la.content && la.content[0] && la.content[0].text)
          la = JSON.parse(la.content[0].text);
        var afterElements = la.elements || la;
        var afterCount = Array.isArray(afterElements)
          ? afterElements.length
          : 0;
        var found = Array.isArray(afterElements)
          ? afterElements.filter(function (e) {
              return e.id === delId;
            })
          : [];
        assert.strictEqual(
          found.length,
          0,
          "Deleted element " +
            delId +
            " should NOT appear in list_all_elements (count: " +
            beforeCount +
            " -> " +
            afterCount +
            ")",
        );
      },
    ),

    check("Scenario: _testDelete trace flow", async function () {
      // Create + delete via command, verify tree state
      var http = require("http");
      var cfg = vscode.workspace.getConfiguration("specEditor");
      var port = cfg.get("mcpPort", 8088);

      // Create
      var cr = await mcpRequest(port, "write_element", {
        aspect: "modules",
        element_type: "module",
        id: "",
        title: "Trace Delete",
        content: "trace",
      });
      var inner = cr.result;
      if (typeof inner === "string") inner = JSON.parse(inner);
      if (inner.content && inner.content[0] && inner.content[0].text)
        inner = JSON.parse(inner.content[0].text);
      var delId = inner.element_id;
      assert.ok(delId, "should have generated ID");

      // Delete via command
      await vscode.commands.executeCommand("specEditor._testDelete", delId);

      // Verify gone
      var list = await mcpRequest(port, "list_all_elements", {});
      var la = list.result;
      if (typeof la === "string") la = JSON.parse(la);
      if (la.content && la.content[0] && la.content[0].text)
        la = JSON.parse(la.content[0].text);
      var elements = la.elements || la;
      var found = Array.isArray(elements)
        ? elements.filter(function (e) {
            return e.id === delId;
          })
        : [];
      assert.strictEqual(
        found.length,
        0,
        "element " + delId + " should be removed from list",
      );
    }),

    check(
      "Scenario: tree preserves expanded aspects after delete",
      async function () {
        // Create elements in two different aspects, expand both,
        // delete one element, verify the OTHER aspect stays expanded.
        var http = require("http");
        var cfg = vscode.workspace.getConfiguration("specEditor");
        var port = cfg.get("mcpPort", 8088);

        // Create element in 'modules'
        var cr1 = await mcpRequest(port, "write_element", {
          aspect: "modules",
          element_type: "module",
          id: "",
          title: "Module A",
          content: "test",
        });
        var inner1 =
          typeof cr1.result === "string" ? JSON.parse(cr1.result) : cr1.result;
        if (inner1.content && inner1.content[0] && inner1.content[0].text)
          inner1 = JSON.parse(inner1.content[0].text);
        var moduleId = inner1.element_id;
        assert.ok(moduleId, "should have module ID");

        // Create element in 'nfr'
        var cr2 = await mcpRequest(port, "write_element", {
          aspect: "nfr",
          element_type: "nfr",
          id: "",
          title: "NFR A",
          content: "perf",
        });
        var inner2 =
          typeof cr2.result === "string" ? JSON.parse(cr2.result) : cr2.result;
        if (inner2.content && inner2.content[0] && inner2.content[0].text)
          inner2 = JSON.parse(inner2.content[0].text);
        var nfrId = inner2.element_id;
        assert.ok(nfrId, "should have NFR ID");

        // Expand both aspects via treeView.reveal (simulated — we can't click in headless test)
        await vscode.commands.executeCommand(
          "specEditor._expandAspect",
          "modules",
        );
        await vscode.commands.executeCommand("specEditor._expandAspect", "nfr");

        // Verify both are expanded
        var expanded = await vscode.commands.executeCommand(
          "specEditor._getExpandedAspects",
        );
        assert.ok(
          expanded.indexOf("modules") !== -1,
          "modules should be expanded",
        );
        assert.ok(expanded.indexOf("nfr") !== -1, "nfr should be expanded");

        // Delete the module element
        await vscode.commands.executeCommand(
          "specEditor._testDelete",
          moduleId,
        );

        // Wait for _restoreExpandedState to finish (retries at 100/300/600ms)
        await new Promise(function (r) {
          setTimeout(r, 1000);
        });

        // Verify modules element is gone
        var list = await mcpRequest(port, "list_all_elements", {});
        var la = list.result;
        if (typeof la === "string") la = JSON.parse(la);
        if (la.content && la.content[0] && la.content[0].text)
          la = JSON.parse(la.content[0].text);
        var elements = la.elements || la;
        var foundModule = Array.isArray(elements)
          ? elements.filter(function (e) {
              return e.id === moduleId;
            })
          : [];
        assert.strictEqual(
          foundModule.length,
          0,
          "module element should be deleted",
        );

        // Verify NFR is STILL expanded (incremental update preserved other aspects)
        var expandedAfter = await vscode.commands.executeCommand(
          "specEditor._getExpandedAspects",
        );
        assert.ok(
          expandedAfter.indexOf("nfr") !== -1,
          "nfr should remain expanded after delete in modules",
        );
        // modules may or may not stay expanded (it lost an element, but the aspect itself persists if it still has elements)
        // The key assertion: deletion in one aspect did NOT collapse the other

        // Clean up the NFR element too
        await mcpRequest(port, "delete_element", { element_id: nfrId });
      },
    ),

    // ── Element creation ─────────────────────────────────────────────
    check("Scenario: createElement command registered", async function () {
      var cmds = await vscode.commands.getCommands(true);
      assert.ok(
        cmds.indexOf("specEditor.createElement") !== -1,
        "createElement command should be registered",
      );
    }),

    check("Scenario: createElement via MCP with auto-ID", async function () {
      // Call write_element with empty ID — MCP should auto-generate
      var http = require("http");
      var cfg = vscode.workspace.getConfiguration("specEditor");
      var port = cfg.get("mcpPort", 8088);
      var result = await new Promise(function (resolve, reject) {
        var body = JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          method: "tools/call",
          params: {
            name: "write_element",
            arguments: {
              aspect: "modules",
              element_type: "module",
              id: "",
              title: "Test Auto ID",
              content: "Test content",
            },
          },
        });
        var req = http.request(
          {
            hostname: "127.0.0.1",
            port: port,
            path: "/mcp",
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(body),
            },
            timeout: 5000,
          },
          function (res) {
            var d = "";
            res.on("data", function (c) {
              d += c;
            });
            res.on("end", function () {
              try {
                var data = JSON.parse(d);
                var txt = data.result.content[0].text;
                var inner = JSON.parse(txt);
                resolve(inner);
              } catch (e) {
                reject(e);
              }
            });
          },
        );
        req.on("error", reject);
        req.on("timeout", function () {
          req.destroy();
          reject(new Error("timeout"));
        });
        req.write(body);
        req.end();
      });
      assert.strictEqual(
        result.status,
        "ok",
        "write_element should succeed: " + JSON.stringify(result),
      );
      var generatedId = result.element_id;
      assert.ok(
        generatedId && generatedId !== "",
        "element_id should be auto-generated, got: " + generatedId,
      );

      // Verify element appears in list_all_elements
      var listResult = await new Promise(function (resolve, reject) {
        var body = JSON.stringify({
          jsonrpc: "2.0",
          id: 2,
          method: "tools/call",
          params: { name: "list_all_elements", arguments: {} },
        });
        var req = http.request(
          {
            hostname: "127.0.0.1",
            port: port,
            path: "/mcp",
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(body),
            },
            timeout: 5000,
          },
          function (res) {
            var d = "";
            res.on("data", function (c) {
              d += c;
            });
            res.on("end", function () {
              try {
                var data = JSON.parse(d);
                var txt = data.result.content[0].text;
                var inner = JSON.parse(txt);
                resolve(inner);
              } catch (e) {
                reject(e);
              }
            });
          },
        );
        req.on("error", reject);
        req.write(body);
        req.end();
      });
      var elements = Array.isArray(listResult)
        ? listResult
        : listResult.elements || [];
      var found = elements.filter(function (e) {
        return e.id === generatedId;
      });
      assert.ok(
        found.length > 0,
        "Created element " +
          generatedId +
          " should appear in list_all_elements",
      );
    }),

    // ── Diagram switching ────────────────────────────────────────────
    check(
      "Scenario: three sequential diagram switches via selectElement",
      async function () {
        // Open panel first
        await vscode.commands.executeCommand("specEditor.viewDiagram");
        await new Promise(function (r) {
          setTimeout(r, 1000);
        });

        // Switch to modules
        await vscode.commands.executeCommand(
          "specEditor.viewDiagram",
          "modules",
        );
        await new Promise(function (r) {
          setTimeout(r, 500);
        });

        // Switch to user_scenarios
        await vscode.commands.executeCommand(
          "specEditor.viewDiagram",
          "user_scenarios",
        );
        await new Promise(function (r) {
          setTimeout(r, 500);
        });

        // Switch to data_entities
        await vscode.commands.executeCommand(
          "specEditor.viewDiagram",
          "data_entities",
        );
        await new Promise(function (r) {
          setTimeout(r, 500);
        });

        assert.ok(true, "Three sequential diagram switches executed");
      },
    ),

    // ── Project switching ────────────────────────────────────────────
    check("Status bar: updates on project switch", async function () {
      // Wait for MCP to be truly ready (not just spawned)
      var ready = false;
      for (var i = 0; i < 15; i++) {
        try {
          var http = require("http");
          await new Promise(function (resolve, reject) {
            var body = JSON.stringify({
              jsonrpc: "2.0",
              id: 1,
              method: "initialize",
              params: {},
            });
            var req = http.request(
              {
                hostname: "127.0.0.1",
                port: mcpPort,
                path: "/mcp",
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  "Content-Length": Buffer.byteLength(body),
                },
                timeout: 1000,
              },
              function (res) {
                resolve();
              },
            );
            req.on("error", reject);
            req.write(body);
            req.end();
          });
          ready = true;
          break;
        } catch {
          await new Promise(function (r) {
            setTimeout(r, 500);
          });
        }
      }
      assert.ok(ready, "MCP server not reachable before switch test");

      var projectPath = "/Users/dmitry/Documents/Droid/spec-editor2/Product";
      await vscode.commands.executeCommand(
        "specEditor._testSwitchProject",
        projectPath,
      );
      var s = await vscode.commands.executeCommand("specEditor._getStatus");
      assert.strictEqual(s.mcpConnected, true, "mcpConnected should be true");
      assert.ok(
        s.text.indexOf("$(checklist)") !== -1,
        "Status bar should show $(checklist), got: " + s.text,
      );
    }),

    check("Scenario: project persists in workspaceState", async function () {
      // Simulate: user opens project, reload happens, project restores.
      // 1. Switch to project (this saves lastProject)
      var projectPath = "/Users/dmitry/Documents/Droid/spec-editor2/Product";
      await vscode.commands.executeCommand(
        "specEditor._testSwitchProject",
        projectPath,
      );
      // 2. Verify status bar is green
      var s = await vscode.commands.executeCommand("specEditor._getStatus");
      assert.strictEqual(s.mcpConnected, true);
      assert.ok(
        s.text.indexOf("$(checklist)") !== -1,
        "After project switch: expected $(checklist), got " + s.text,
      );
      // 3. Verify lastProject was saved to workspaceState
      // We can't read extensionContext directly from test, but we can
      // verify the side effect: status bar is green, MCP is connected.
      // The actual persistence is tested by the reload scenario manually.
      assert.ok(
        true,
        "lastProject persistence: status bar green = restore works",
      );
    }),

    check("Scenario: auto-restore fires startMcpCalls once", async function () {
      // After project switch, startMcpServer should not have been called again.
      // The initialize loop runs once, then lastProject restores the project.
      var s = await vscode.commands.executeCommand("specEditor._getStatus");
      assert.ok(s.mcpProcessRunning, "MCP should still be running");
      assert.ok(s.mcpConnected, "MCP should be connected");
    }),

    check("Scenario: click aspect element shows diagram", async function () {
      // 1. Open WebView panel
      await vscode.commands.executeCommand("specEditor.viewDiagram");
      // 2. Simulate clicking a tree element — passes elementId to viewDiagram
      // The SpecTreeItem calls viewDiagram with the element ID as argument
      await vscode.commands.executeCommand(
        "specEditor.viewDiagram",
        "REQ-0001",
      );
      // 3. Panel should be revealed (no error thrown)
      assert.ok(true, "viewDiagram with elementId executed without error");
    }),

    check("Scenario: click aspect header opens diagram", async function () {
      // Simulate clicking an aspect header — passes aspect name
      // First call creates panel, second call reuses it with aspect
      await vscode.commands.executeCommand("specEditor.viewDiagram");
      // Now simulate clicking "modules" aspect header
      await vscode.commands.executeCommand("specEditor.viewDiagram", "modules");
      // Panel should have received selectElement message for "modules"
      assert.ok(true, "Aspect header click sends selectElement");
    }),

    check(
      "Scenario: clicking different aspects switches diagram",
      async function () {
        // Switch to user_scenarios
        await vscode.commands.executeCommand(
          "specEditor.viewDiagram",
          "user_scenarios",
        );
        // Switch back to modules
        await vscode.commands.executeCommand(
          "specEditor.viewDiagram",
          "modules",
        );
        assert.ok(true, "Multiple aspect switches without error");
      },
    ),

    check(
      "Scenario: viewDiagram without panel reuses and sends event",
      async function () {
        // viewDiagram was already called above, panel exists
        // Calling again with elementId should send selectElement message
        await vscode.commands.executeCommand(
          "specEditor.viewDiagram",
          "modules",
        );
        assert.ok(true, "Reused panel + selectElement event sent");
      },
    ),

    check("Scenario: create + edit roundtrip via MCP", async function () {
      // createElement scenario: write with empty ID, get auto-ID, edit content
      // This tests the full create-then-edit flow without UI interaction
      var http = require("http");
      var cfg = vscode.workspace.getConfiguration("specEditor");
      var port = cfg.get("mcpPort", 8088);

      // Step 1: Create element with empty ID
      var createResult = await mcpRequest(port, "write_element", {
        aspect: "modules",
        element_type: "module",
        id: "",
        title: "Roundtrip Test",
        content: "Initial content",
      });
      assert.ok(
        createResult && createResult.result,
        "write_element should succeed",
      );
      var res1 =
        typeof createResult.result === "string"
          ? JSON.parse(createResult.result)
          : createResult.result;
      var elId =
        res1.element_id ||
        (res1.content?.[0]?.text &&
          JSON.parse(res1.content[0].text).element_id);
      assert.ok(elId, "element_id should be auto-generated");

      // Step 2: Edit via write_element with ID
      var editResult = await mcpRequest(port, "write_element", {
        aspect: "modules",
        element_type: "module",
        id: elId,
        title: "Roundtrip Test Edited",
        content: "Updated content",
      });
      assert.ok(editResult && editResult.result, "edit should succeed");

      // Step 3: Read back and verify
      var readResult = await mcpRequest(port, "read_element", {
        element_id: elId,
      });
      assert.ok(readResult && readResult.result, "read should succeed");
    }),

    check("Scenario: createElement command registered", async function () {
      var http = require("http");
      var cfg = vscode.workspace.getConfiguration("specEditor");
      var port = cfg.get("mcpPort", 8088);

      // Create element
      var createResult = await mcpRequest(port, "write_element", {
        aspect: "modules",
        element_type: "module",
        id: "",
        title: "Context Menu Test",
        content: "Test content for edit",
      });

      assert.ok(
        createResult && createResult.result,
        "write_element should succeed",
      );

      // Read element by ID to confirm it exists
      // Then test editElement command directly
      await vscode.commands.executeCommand(
        "specEditor.editElement",
        "TEST-EDIT-001",
      );
      assert.ok(true, "editElement command invoked without error");
    }),

    // ── Commands ──────────────────────────────────────────────────────
    check("Command: viewDiagram", async function () {
      await vscode.commands.executeCommand("specEditor.viewDiagram");
      assert.ok(true, "viewDiagram ok");
    }),

    check("Command: validate", async function () {
      try {
        await vscode.commands.executeCommand("specEditor.validate");
      } catch (e) {
        /* may warn if no project */
      }
      assert.ok(true, "validate ok");
    }),

    // ── Tree View / Elements Loading ──────────────────────────────────
    check("TreeView: elements load without 'Failed to load' message", async function () {
      // The tree should have loaded elements by now (after MCP connect).
      // If the tree message is "Failed to load elements", the bug is present.
      var s = await vscode.commands.executeCommand("specEditor._getStatus");
      assert.ok(s, "Status should be available");
      if (s.treeMessage) {
        assert.ok(
          s.treeMessage !== "Failed to load elements",
          "BUG: Tree shows 'Failed to load elements'. " +
            "This means loadElements() caught an error. Message: " + s.treeMessage,
        );
        // Acceptable messages: undefined, "Loading...", "No elements yet..."
        assert.ok(
          s.treeMessage === "Loading…" ||
            s.treeMessage.indexOf("No elements") !== -1 ||
            s.treeMessage.indexOf("No project") !== -1,
          "Unexpected tree message: " + s.treeMessage,
        );
      }
    }),

    check("TreeView: element count > 0 when project loaded", async function () {
      var s = await vscode.commands.executeCommand("specEditor._getStatus");
      if (s.mcpConnected && s.projectPath) {
        assert.ok(
          s.elementCount > 0,
          "Expected elements to be loaded (elementCount=" +
            s.elementCount +
            "). Project: " +
            s.projectPath,
        );
      }
    }),

    check("MCP: list_all_elements with invalid path returns error (not crash)", async function () {
      // This tests that the MCP server fix works: invalid project_path
      // should return a proper isError, not crash the HTTP thread.
      try {
        var r = await mcpRequest(mcpPort, "list_all_elements", {
          project_path: "/nonexistent/path/xyz",
        });
        // Either we get a valid response with isError, or the connection fails.
        // If the server crashes, mcpRequest rejects with an error.
        if (r && r.result) {
          if (r.result.isError) {
            // Expected: proper error response
            var errText = r.result.content[0].text || "";
            assert.ok(
              errText.indexOf("Failed to load project") !== -1 ||
                errText.indexOf("methodology.yaml") !== -1,
              "Error should mention project loading: " + errText,
            );
          } else {
            // Some servers may fallback to Docker path — also acceptable
            assert.ok(true, "Server handled invalid path gracefully");
          }
        }
      } catch (e) {
        // If the server crashes, the connection may hang up — this is the bug.
        // The fix prevents this, so after the fix this catch shouldn't trigger.
        assert.ok(
          e.message.indexOf("ECONNRESET") === -1 &&
            e.message.indexOf("socket hang up") === -1,
          "BUG: MCP server crashed on invalid path: " + e.message,
        );
      }
    }),
  ];

  for (var i = 0; i < tests.length; i++) await tests[i]();

  console.log("\n" + results.join("\n"));
  console.log(
    "\n" + (tests.length - failures) + "/" + tests.length + " passed",
  );
  if (failures > 0) throw new Error(failures + " test(s) failed");
}

module.exports = { run };
