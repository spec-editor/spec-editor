import * as vscode from "vscode";
import { ChildProcess, spawn } from "child_process";
import * as path from "path";
import * as fs_log from "fs";

const MCP_PORT = 8088;

// ── Tracing & UI delay constants ──────────────────────────────────────────
// Single source of truth for delays after user actions that trigger
// asynchronous tree/diagram updates. Use these instead of magic numbers.
const UI_DELAY = {
  /** After fire(undefined) — wait for VSCode to re-render the tree. */
  TREE_REFRESH: 100,
  /** Delay before first reveal() attempt after tree refresh. */
  TREE_RESTORE_FIRST: 100,
  /** Second retry for reveal() */
  TREE_RESTORE_SECOND: 300,
  /** Final retry for reveal() */
  TREE_RESTORE_FINAL: 600,
  /** After delete — total wait before reading expanded state in tests. */
  DELETE_RESTORE_WAIT: 1000,
  /** After project switch — wait for loadElements to finish. */
  PROJECT_SWITCH: 2000,
  /** After MCP server start — wait for port to be ready. */
  MCP_READY_POLL: 300,
  /** Max MCP ready wait cycles */
  MCP_READY_CYCLES: 15,
  /** Between MCP call retries. */
  MCP_RETRY: 500,
  /** Docker mode health-check interval. */
  DOCKER_CHECK: 2000,
  /** MCP auto-restart delay after crash. */
  MCP_RESTART: 2000,
  /** Dialog timeout for showOpenDialog. */
  DIALOG_TIMEOUT: 30000,
  /** How long the green Start button stays visible after changes. */
  PENDING_LOAD_DELAY: 2000,
} as const;

// Log file with timestamp so each run gets its own file.
// Format: /tmp/spec-editor-2026-06-14T12-34-56.log
function makeLogPath(): string {
  const prefix = process.env.VSCODE_TEST ? "spec-editor-test" : "spec-editor";
  const ts = new Date().toISOString().replace(/:/g, "-").replace(/\..+/, "");
  return `/tmp/${prefix}-${ts}.log`;
}

// Symlink to the latest log for convenience: tail -f /tmp/spec-editor-latest.log
function updateLatestSymlink(logPath: string): void {
  const latest = process.env.VSCODE_TEST
    ? "/tmp/spec-editor-test-latest.log"
    : "/tmp/spec-editor-latest.log";
  try {
    fs_log.unlinkSync(latest);
  } catch {
    // doesn't exist yet
  }
  try {
    fs_log.symlinkSync(logPath, latest);
  } catch {
    // symlink may fail on some platforms — ignore
  }
}

const LOG_FILE = makeLogPath();
updateLatestSymlink(LOG_FILE);

// Re-init log file on each activation (VSCode caches extension modules,
// so module-level code only runs once. activate() is called each time).
let _logFile: string = LOG_FILE;
let _logInitDone = false;

function initLogging(): void {
  // Only create a new log file on the FIRST activate call.
  // Subsequent hot-reloads reuse the same file.
  if (!_logInitDone) {
    _logInitDone = true;
    return; // LOG_FILE already set at module load
  }
  // Hot reload: create a fresh log file for this session.
  _logFile = makeLogPath();
  updateLatestSymlink(_logFile);
  // Write a marker so we can confirm logging works.
  try {
    fs_log.appendFileSync(
      _logFile,
      `[${new Date().toISOString()}] START Logging initialized\n`,
    );
  } catch {
    // ignore
  }
}

function logEvent(level: string, msg: string): void {
  const ts = new Date().toISOString();
  const line = `[${ts}] ${level} ${msg}\n`;
  try {
    fs_log.appendFileSync(_logFile, line);
  } catch {
    // ignore
  }
  // Always write to output channel for headless test visibility
  try {
    if (outputChannel) {
      outputChannel.info(`${level} ${msg}`);
    }
  } catch {
    // ignore
  }
  // Also write to a known trace file for test debugging
  try {
    const traceFile = "/tmp/spec-editor-trace.log";
    fs_log.appendFileSync(traceFile, line);
  } catch {
    // ignore
  }
}

let mcpProcess: ChildProcess | undefined;
let mcpPort: number = MCP_PORT;
let activeMcpPort: number = MCP_PORT;
let pythonPath: string = "python3";
let detectedPythonPath: string = "";
let detectionTrace: string[] = [];
let outputChannel: vscode.LogOutputChannel;
let mcpReady: Promise<void> = Promise.resolve();
let extensionContext: vscode.ExtensionContext;
let treeProvider: SpecTreeProvider;
let treeView: vscode.TreeView<SpecTreeItem>;
let filterAspect: string = "";
let filterRelation: string = "";
let activePanel: vscode.WebviewPanel | undefined;
let statusBar: vscode.StatusBarItem;
let mcpConnected: boolean = false;
let mcpStatusTooltip: string = "";
let runTerminal: vscode.Terminal | undefined;
let runActive: boolean = false;
let sseReconnectTimer: NodeJS.Timeout | undefined;
let sseDebounceTimer: NodeJS.Timeout | undefined;

function _setPendingChanges(): void {
  logEvent("TRACE", "_setPendingChanges: setting pendingChanges=true");
  outputChannel.info("Elements updated — pending changes set");
  vscode.commands.executeCommand(
    "setContext",
    "specEditor.pendingChanges",
    true,
  );
  // Notify WebView so diagram can auto-refresh or show Update button
  if (activePanel) {
    activePanel.webview.postMessage({
      type: "specEditor",
      event: "elementsChanged",
    });
  }
}

let tempElementFiles: Map<
  string,
  { aspect: string; elementType: string; id: string; title: string }
> = new Map();

function parseYamlFrontmatter(raw: string): Record<string, string> {
  const result: Record<string, string> = {};
  const lines = raw.split("\n");
  let inYaml = false;
  let currentKey = "";
  let currentValue = "";
  for (const line of lines) {
    if (line === "---") {
      if (!inYaml) {
        inYaml = true;
        continue;
      } else break;
    }
    if (!inYaml) continue;
    const match = line.match(/^(\w+):\s*(.*)$/);
    if (match) {
      if (currentKey) result[currentKey] = currentValue.trim();
      currentKey = match[1];
      currentValue = match[2];
    } else if (currentKey) {
      currentValue += "\n" + line;
    }
  }
  if (currentKey) result[currentKey] = currentValue.trim();
  // Unquote
  for (const key of Object.keys(result)) {
    let v = result[key];
    if (
      (v.startsWith("'") && v.endsWith("'")) ||
      (v.startsWith('"') && v.endsWith('"'))
    ) {
      v = v.slice(1, -1);
    }
    result[key] = v;
  }
  return result;
}

async function syncAgentConfig(): Promise<void> {
  const wsRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!wsRoot) return;
  const agentsPath: string = path.join(wsRoot, "agents.yaml");
  if (!require("fs").existsSync(agentsPath)) return;
  const cfg: vscode.WorkspaceConfiguration =
    vscode.workspace.getConfiguration("specEditor");
  const readAgent = (pfx: string) => ({
    provider: cfg.get<string>(`${pfx}.provider`),
    model: cfg.get<string>(`${pfx}.model`),
    temperature: cfg.get<number>(`${pfx}.temperature`),
    max_tokens: cfg.get<number>(`${pfx}.maxTokens`),
  });
  const yaml: string =
    [
      "agents:",
      "  agent_1:",
      ...Object.entries(readAgent("reasoningModel")).map(([k, v]) => `    ${k}: ${v}`),
      "  agent_2:",
      ...Object.entries(readAgent("chatModel")).map(([k, v]) => `    ${k}: ${v}`),
      "  orchestrator:",
      ...Object.entries(readAgent("orchestrator")).map(
        ([k, v]) => `    ${k}: ${v}`,
      ),
      "max_rounds: 20",
      "max_time_minutes: 30",
    ].join("\n") + "\n";
  require("fs").writeFileSync(agentsPath, yaml);
  outputChannel.info("agents.yaml synced from VSCode settings");
  logEvent("INFO", "agents.yaml synced");
  if (mcpProcess) {
    mcpProcess.kill();
    mcpProcess = undefined;
  }
  const config: vscode.WorkspaceConfiguration =
    vscode.workspace.getConfiguration("specEditor");
  mcpReady = startMcpServer(config).catch((err: Error) =>
    outputChannel.error(`Failed to restart MCP: ${err.message}`),
  );
}

function notifyWebView(event: string): void {
  if (activePanel) {
    activePanel.webview.postMessage({ type: "specEditor", event });
  }
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  initLogging();
  extensionContext = context;
  outputChannel = vscode.window.createOutputChannel("Spec Editor", {
    log: true,
  });
  outputChannel.info("Spec Editor extension activated");

  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(async (doc: vscode.TextDocument) => {
      const fp: string = doc.uri.fsPath;
      const info = tempElementFiles.get(fp);

      if (info) {
        // Temp file edited via createElement/editElement
        const rawContent: string = doc.getText();
        const bodyContent = rawContent.replace(/^---\n[\s\S]*?\n---\n*/, "");
        const yaml = parseYamlFrontmatter(rawContent);
        const title = yaml["title"] || info.title;
        const parent = yaml["parent"] || undefined;
        try {
          await callMcp("write_element", {
            aspect: info.aspect,
            element_type: info.elementType,
            id: info.id,
            title,
            content: bodyContent,
            parent,
          });
          logEvent("INFO", `createElement: saved ${info.id}`);
          // Show update indicator + pending changes flag
          treeView.message = `$(sync~spin) Updating ${info.id}...`;
          _setPendingChanges();
          setTimeout(() => {
            treeProvider.loadElements();
          }, UI_DELAY.TREE_REFRESH);
        } catch (e: any) {
          outputChannel.warn(`Failed to sync ${info.id}: ${e.message}`);
        }
        return;
      }

      // Temp file opened directly (not through editElement command)
      if (fp.includes("/spec-editor-temp/") && fp.endsWith(".md")) {
        const fileName: string = path.basename(fp, ".md");
        const match = fileName.match(/^([A-Z]+-\S+)/);
        if (match) {
          const elementId: string = match[1];
          const rawContent: string = doc.getText();
          const bodyContent = rawContent.replace(/^---\n[\s\S]*?\n---\n*/, "");
          const yaml = parseYamlFrontmatter(rawContent);
          const aspect = yaml["aspect"] || "";
          const elementType = yaml["element_type"] || "";
          const title = yaml["title"] || elementId;
          const parent = yaml["parent"] || undefined;
          if (aspect && elementType) {
            try {
              await callMcp("write_element", {
                aspect,
                element_type: elementType,
                id: elementId,
                title,
                content: bodyContent,
                parent,
              });
              logEvent("INFO", `tempSave: synced ${elementId}`);
              treeView.message = `$(sync~spin) Syncing ${elementId}...`;
              _setPendingChanges();
              setTimeout(() => {
                treeProvider.loadElements();
              }, UI_DELAY.TREE_REFRESH);
            } catch (e: any) {
              outputChannel.warn(
                `Failed to sync temp file ${elementId}: ${e.message}`,
              );
            }
          }
        }
        return;
      }

      // Any .md file in aspects/ folder — edited externally (e.g. opened from tree)
      if (fp.includes("/aspects/") && fp.endsWith(".md")) {
        const fileName: string = path.basename(fp, ".md");
        const match = fileName.match(/^([A-Z]+-\S+)/);
        if (match) {
          const elementId: string = match[1];
          const rawContent: string = doc.getText();
          const bodyContent = rawContent.replace(/^---\n[\s\S]*?\n---\n*/, "");
          const yaml = parseYamlFrontmatter(rawContent);
          const aspect = yaml["aspect"] || "";
          const elementType = yaml["element_type"] || "";
          const title = yaml["title"] || fileName;
          const parent = yaml["parent"] || undefined;
          if (aspect && elementType) {
            try {
              await callMcp("write_element", {
                aspect,
                element_type: elementType,
                id: elementId,
                title,
                content: bodyContent,
                parent,
              });
              logEvent(
                "INFO",
                `externalSave: synced ${elementId} in ${aspect}`,
              );
              treeView.message = `$(sync~spin) Syncing ${elementId}...`;
              _setPendingChanges();
              setTimeout(() => {
                treeProvider.loadElements();
              }, UI_DELAY.TREE_REFRESH);
            } catch (e: any) {
              outputChannel.warn(
                `Failed to sync external file ${elementId}: ${e.message}`,
              );
            }
          }
        }
      }
    }),
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(
      async (e: vscode.ConfigurationChangeEvent) => {
        if (!e.affectsConfiguration("specEditor")) return;
        logEvent("INFO", "Config changed: syncing to agents.yaml");
        await syncAgentConfig();
      },
    ),
  );

  logEvent("START", "Extension activated");

  // Auto-configure MCP server for Copilot on first activation
  await _ensureMcpJson();

  // Init context keys to safe defaults
  vscode.commands.executeCommand(
    "setContext",
    "specEditor.pendingChanges",
    false,
  );
  vscode.commands.executeCommand("setContext", "specEditor.runActive", false);

  statusBar = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100,
  );
  statusBar.text = "$(error) Spec Editor";
  logEvent("INFO", "StatusBar init: $(error) (activate)");
  statusBar.tooltip = "MCP server not connected — click to open project";
  statusBar.command = "specEditor._quickOpen";
  statusBar.color = new vscode.ThemeColor("statusBarItem.errorForeground");
  statusBar.show();

  // Register showLog command — opens latest log file
  context.subscriptions.push(
    vscode.commands.registerCommand("specEditor.showLog", async () => {
      const latestLog = "/tmp/spec-editor-latest.log";
      if (require("fs").existsSync(latestLog)) {
        const doc: vscode.TextDocument =
          await vscode.workspace.openTextDocument(vscode.Uri.file(latestLog));
        vscode.window.showTextDocument(doc, { preview: false });
      } else {
        vscode.window.showInformationMessage(
          "No log file found. The extension creates /tmp/spec-editor-latest.log after activation.",
        );
      }
    }),
    vscode.commands.registerCommand("specEditor.filterByAspect", async () => {
      const aspects = ["all", ...treeProvider._aspectOrder];
      const current = filterAspect || "all";
      const picked = await vscode.window.showQuickPick(aspects, {
        placeHolder: `Filter by aspect (current: ${current})`,
      });
      if (picked !== undefined) {
        filterAspect = picked === "all" ? "" : picked;
        treeProvider.loadElements();
        logEvent("INFO", `filterByAspect: ${filterAspect || "all"}`);
      }
    }),
    vscode.commands.registerCommand("specEditor.filterByRelation", async () => {
      // Collect all relationship types across elements
      const relTypes = new Set<string>();
      for (const el of treeProvider.elements) {
        if (el.relationships) {
          for (const rt of Object.keys(el.relationships)) {
            relTypes.add(rt);
          }
        }
      }
      const options = ["all", ...relTypes].sort();
      const current = filterRelation || "all";
      const picked = await vscode.window.showQuickPick(options, {
        placeHolder: `Filter by relation type (current: ${current})`,
      });
      if (picked !== undefined) {
        filterRelation = picked === "all" ? "" : picked;
        treeProvider.loadElements();
        logEvent("INFO", `filterByRelation: ${filterRelation || "all"}`);
      }
    }),
    vscode.commands.registerCommand("specEditor.refreshTree", async () => {
      logEvent("INFO", "refreshTree: manual refresh triggered");
      await treeProvider.loadElements();
      vscode.window.showInformationMessage(
        `Tree refreshed: ${treeProvider.elements.length} elements`,
      );
    }),
  );

  // File watcher: auto-refresh tree when aspects/ files change on disk
  const aspectsWatcher: vscode.FileSystemWatcher =
    vscode.workspace.createFileSystemWatcher("**/aspects/**/*.md");
  let _refreshTimer: ReturnType<typeof setTimeout> | null = null;
  const debouncedRefresh = (): void => {
    if (_refreshTimer) clearTimeout(_refreshTimer);
    _refreshTimer = setTimeout(() => {
      logEvent("INFO", "refreshTree: aspects file changed on disk");
      treeProvider.loadElements();
    }, 1000); // debounce 1s
  };
  aspectsWatcher.onDidCreate(debouncedRefresh);
  aspectsWatcher.onDidChange(debouncedRefresh);
  aspectsWatcher.onDidDelete(debouncedRefresh);
  context.subscriptions.push(aspectsWatcher);

  context.subscriptions.push(
    vscode.commands.registerCommand("specEditor.open", handleOpenProject),
    vscode.commands.registerCommand("specEditor.newProject", handleNewProject),
    vscode.commands.registerCommand(
      "specEditor.viewDiagram",
      handleViewDiagram,
    ),
    vscode.commands.registerCommand("specEditor.resetProject", async () => {
      const confirm = await vscode.window.showWarningMessage(
        "Reset all aspects except sources? This deletes all generated specification elements.",
        { modal: true },
        "Reset",
      );
      if (confirm !== "Reset") return;

      try {
        const elementsStr: any = await callMcp("list_all_elements", {});
        const data =
          typeof elementsStr === "string"
            ? JSON.parse(elementsStr)
            : elementsStr;
        const elements = data.elements || data;
        let deleted = 0;
        for (const el of elements) {
          if (!el.id.startsWith("SRC-")) {
            try {
              await callMcp("delete_element", {
                element_id: el.id,
                force: true,
              });
              deleted++;
            } catch {}
          }
        }
        treeProvider.loadElements();
        // Clean up run artifacts (checkpoint + log)
        const wsRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (wsRoot) {
          const fs = require("fs");
          const path = require("path");
          const checkpointFile = path.join(
            wsRoot,
            ".spec-editor-checkpoint.json",
          );
          const runLogFile = path.join(wsRoot, ".spec-editor-run.log");
          try {
            fs.unlinkSync(checkpointFile);
          } catch {}
          try {
            fs.unlinkSync(runLogFile);
          } catch {}
        }
        vscode.window.showInformationMessage(
          `Reset complete: ${deleted} elements deleted. Sources preserved.`,
        );
      } catch (e: any) {
        vscode.window.showErrorMessage(`Reset failed: ${e.message}`);
      }
    }),
    vscode.commands.registerCommand("specEditor.createElement", async () => {
      let aspects: string[] = [];
      try {
        const methodData: string = await callMcp("get_methodology", {});
        const data2: any = JSON.parse(methodData);
        aspects = (data2.aspects || []).map((a: any) => a.name);
      } catch {
        aspects = [
          "modules",
          "user_scenarios",
          "user_interface",
          "data_entities",
          "nfr",
          "implementation",
          "metrics",
          "sources",
        ];
      }
      if (aspects.length === 0) {
        vscode.window.showErrorMessage(
          "No aspects found. Create a project first.",
        );
        return;
      }
      const aspect: string | undefined = await vscode.window.showQuickPick(
        aspects,
        {
          placeHolder: "Select aspect for new element",
        },
      );
      if (!aspect) return;

      let elementType: string = "module";
      try {
        const methodData: string = await callMcp("get_methodology", {});
        const md: any = JSON.parse(methodData);
        const asp = (md.aspects || []).find((a: any) => a.name === aspect);
        if (asp?.element_types?.length > 0) {
          elementType = asp.element_types[0].name;
        }
      } catch {
        // ignore, use default
      }

      const title: string | undefined = await vscode.window.showInputBox({
        prompt: "Element title",
        placeHolder: "My new element",
      });
      if (title === undefined) return;

      const result: string = await callMcp("write_element", {
        aspect,
        element_type: elementType,
        id: "",
        title,
        content: "",
      });
      const data: any = JSON.parse(result);
      const generatedId: string = data.element_id || "unknown";

      const tmpDir: string = path.join(
        require("os").tmpdir(),
        "spec-editor-temp",
      );
      require("fs").mkdirSync(tmpDir, { recursive: true });
      const tmpFile: string = path.join(tmpDir, `${generatedId}.md`);
      const template: string = `---
id: ${generatedId}
aspect: ${aspect}
element_type: ${elementType}
title: ${title}
status: draft

---

(Write your content here)
`;
      require("fs").writeFileSync(tmpFile, template);
      tempElementFiles.set(tmpFile, {
        aspect,
        elementType,
        id: generatedId,
        title,
      });

      const doc: vscode.TextDocument = await vscode.workspace.openTextDocument(
        vscode.Uri.file(tmpFile),
      );
      vscode.window.showTextDocument(doc);

      treeProvider.loadElements();
      outputChannel.info(`Created element ${generatedId} in ${aspect}`);
      logEvent("INFO", `createElement: ${generatedId} / ${aspect}`);
    }),
    vscode.commands.registerCommand("specEditor._openMethodology", async () => {
      const wsRoot: string | undefined =
        vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!wsRoot) return;
      const methodPath: vscode.Uri = vscode.Uri.file(
        path.join(wsRoot, "methodology.yaml"),
      );
      const doc: vscode.TextDocument =
        await vscode.workspace.openTextDocument(methodPath);
      vscode.window.showTextDocument(doc);
    }),
    vscode.commands.registerCommand(
      "specEditor.selectMethodology",
      async () => {
        const wsRoot: string | undefined =
          vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!wsRoot) return;

        const methodPath: string = path.join(wsRoot, "methodology.yaml");
        const exists: boolean = require("fs").existsSync(methodPath);

        if (exists) {
          const confirm: string | undefined =
            await vscode.window.showWarningMessage(
              "A methodology.yaml already exists. Selecting a new methodology will OVERWRITE the file and replace all aspect definitions. Any custom aspect configuration will be lost. Continue?",
              { modal: true },
              "Overwrite",
            );
          if (confirm !== "Overwrite") return;
        }

        const methodologies: string[] = ["waterfall", "agile", "api_first"];
        const method: string | undefined = await vscode.window.showQuickPick(
          methodologies,
          { placeHolder: "Select methodology" },
        );
        if (!method) return;

        const template: string = getMethodologyTemplate(method);
        require("fs").writeFileSync(methodPath, template);
        require("fs").mkdirSync(path.join(wsRoot, "aspects"), {
          recursive: true,
        });

        vscode.window.showInformationMessage(
          `Methodology set to ${method}. Tree will reload.`,
        );
        treeProvider.loadElements();
        logEvent("INFO", `selectMethodology: ${method}`);
      },
    ),
    vscode.commands.registerCommand("specEditor.validate", handleValidate),
    vscode.commands.registerCommand("specEditor.startMcp", async () => {
      const cfg: vscode.WorkspaceConfiguration =
        vscode.workspace.getConfiguration("specEditor");
      try {
        await startMcpServer(cfg);
      } catch (err: any) {
        vscode.window.showErrorMessage(
          `MCP server failed to start: ${err.message}`,
        );
      }
    }),
    vscode.commands.registerCommand(
      "specEditor._testDelete",
      async (elementId: string) => {
        logEvent("TRACE", `_testDelete: START id=${elementId}`);
        if (!elementId) return;
        await callMcp("delete_element", { element_id: elementId });
        logEvent("TRACE", `_testDelete: MCP deleted ${elementId}`);
        treeProvider.removeElement(elementId);
        logEvent("TRACE", `_testDelete: removeElement called`);
        const elsAfter: string = await callMcp("list_all_elements", {});
        const data: any = JSON.parse(elsAfter);
        const list: any[] = data.elements || [];
        const found: any[] = list.filter((e: any) => e.id === elementId);
        logEvent(
          "TRACE",
          `_testDelete: list has ${list.length} elements, deleted found=${found.length}`,
        );
      },
    ),
    // Internal: expose expanded aspects for E2E testing
    vscode.commands.registerCommand("specEditor._getExpandedAspects", () => {
      return [...treeProvider._expandedAspects];
    }),
    // Internal: programmatically expand an aspect for E2E testing
    vscode.commands.registerCommand(
      "specEditor._expandAspect",
      (aspect: string) => {
        treeProvider.expandAspect(aspect);
      },
    ),
    vscode.commands.registerCommand(
      "specEditor._testSwitchProject",
      async (projectPath: string) => {
        // Multi-project: project_path is auto-injected by callMcp.
        // Just reload elements to verify the project is accessible.
        treeProvider.loadElements();
        mcpConnected = true;
        mcpStatusTooltip = `MCP server on port ${activeMcpPort || mcpPort}`;
        statusBar.text = "$(checklist) Spec Editor";
        statusBar.tooltip = mcpStatusTooltip;
        statusBar.color = undefined;
        logEvent(
          "OK",
          `_testSwitchProject: status bar updated for ${projectPath}`,
        );
      },
    ),
    vscode.commands.registerCommand(
      "specEditor.editElement",
      async (...args: any[]) => {
        let elementId: string = args[0];
        if (typeof elementId !== "string") {
          // Context menu passes SpecTreeItem as first arg.
          const item: SpecTreeItem | undefined = args[0];
          // Use item.id (set to element ID in constructor).
          elementId = item?.id || "";
          if (!elementId) {
            // Fallback: parse from tooltip
            elementId = args[0]?.tooltip?.match(/[A-Z]+-\d+/)?.[0] || "";
          }
        }
        if (!elementId) return;
        const tmpDir: string = path.join(
          require("os").tmpdir(),
          "spec-editor-temp",
        );
        require("fs").mkdirSync(tmpDir, { recursive: true });
        const tmpFile: string = path.join(tmpDir, `${elementId}.md`);
        // Check if already open — visible editors
        const tmpUri = vscode.Uri.file(tmpFile);
        // Check all open documents
        var realTmp = tmpFile;
        try {
          if (require("fs").existsSync(tmpFile)) {
            realTmp = require("fs").realpathSync(tmpFile);
          }
        } catch {
          realTmp = tmpFile;
        }
        logEvent("TRACE", `editElement: realTmp=${realTmp}`);
        for (const doc of vscode.workspace.textDocuments) {
          if (doc.isClosed) continue;
          var docPath = "";
          try {
            docPath = require("fs").realpathSync(doc.uri.fsPath);
          } catch {
            docPath = doc.uri.fsPath;
          }
          logEvent("TRACE", `editElement: check doc=${docPath}`);
          if (docPath === realTmp) {
            logEvent("TRACE", `editElement: FOUND existing doc, focusing`);
            await vscode.window.showTextDocument(doc, {
              preserveFocus: true,
              viewColumn: vscode.ViewColumn.One,
            });
            return;
          }
        }
        try {
          const raw: string = await callMcp("read_element", {
            element_id: elementId,
          });
          const el: any = JSON.parse(raw);

          // Build YAML frontmatter with all relationships
          let yaml = `---
id: ${elementId}
aspect: ${el.aspect || ""}
element_type: ${el.element_type || "module"}
title: ${el.title || elementId}
status: ${el.status || "draft"}
`;
          if (el.parent) yaml += `parent: ${el.parent}\n`;
          if (el.children?.length)
            yaml += `children:\n${el.children.map((c: string) => `  - ${c}`).join("\n")}\n`;
          if (el.derived_from?.length)
            yaml += `derived_from:\n${el.derived_from.map((d: string) => `  - ${d}`).join("\n")}\n`;
          if (el.tags?.length)
            yaml += `tags:\n${el.tags.map((t: string) => `  - ${t}`).join("\n")}\n`;
          if (el.relationships && Object.keys(el.relationships).length > 0) {
            yaml += "relationships:\n";
            for (const [rtype, entries] of Object.entries(el.relationships)) {
              yaml += `  ${rtype}:\n`;
              for (const e of entries as any[]) {
                yaml += `    - role: ${e.role}\n      target: ${e.target}\n`;
              }
            }
          }
          yaml += `---\n\n${el.content || ""}\n`;

          require("fs").writeFileSync(tmpFile, yaml);
          tempElementFiles.set(tmpFile, {
            aspect: el.aspect || "",
            elementType: el.element_type || "module",
            id: elementId,
            title: el.title || elementId,
          });
          const doc: vscode.TextDocument =
            await vscode.workspace.openTextDocument(vscode.Uri.file(tmpFile));
          vscode.window.showTextDocument(doc);
        } catch (e: any) {
          vscode.window.showErrorMessage(`Edit failed: ${e.message}`);
        }
      },
    ),
    vscode.commands.registerCommand(
      "specEditor.deleteElement",
      async (...args: any[]) => {
        outputChannel.info(
          "deleteElement: CALLED args=" +
            JSON.stringify(
              args.map((a: any) =>
                typeof a === "object" ? a?.id || a?.label : a,
              ),
            ),
        );
        logEvent("INFO", `deleteElement: CALLED with ${args.length} args`);

        // Collect element IDs from all args (clicked item + multi-selection)
        const ids: string[] = [];
        for (const arg of args) {
          if (!arg) continue;
          if (Array.isArray(arg)) {
            // Multi-select: arg is array of SpecTreeItem
            for (const item of arg) {
              const id =
                typeof item === "string"
                  ? item
                  : item?.id || item?.context?.elementId || "";
              if (id) ids.push(id);
            }
          } else if (typeof arg === "string") {
            ids.push(arg);
          } else if (typeof arg === "object") {
            const id = arg?.id || arg?.context?.elementId || "";
            if (id) ids.push(id);
          }
        }

        // Deduplicate
        const uniqueIds = [...new Set(ids.filter(Boolean))];
        if (uniqueIds.length === 0) return;

        const label =
          uniqueIds.length === 1
            ? `element ${uniqueIds[0]}`
            : `${uniqueIds.length} elements`;

        const confirm: string | undefined =
          await vscode.window.showWarningMessage(
            `Delete ${label}? This cannot be undone.`,
            { modal: true },
            "Delete",
          );
        if (confirm !== "Delete") {
          logEvent(
            "INFO",
            `deleteElement: cancelled for ${uniqueIds.length} elements`,
          );
          return;
        }

        let deleted = 0;
        let failed = 0;
        for (const elementId of uniqueIds) {
          try {
            const tmpFile: string = path.join(
              require("os").tmpdir(),
              "spec-editor-temp",
              `${elementId}.md`,
            );
            if (require("fs").existsSync(tmpFile)) {
              require("fs").unlinkSync(tmpFile);
              tempElementFiles.delete(tmpFile);
            }
            logEvent("INFO", `deleteElement: confirming ${elementId}`);
            const mcpResponse: string = await callMcp("delete_element", {
              element_id: elementId,
              force: true,
            });
            logEvent(
              "TRACE",
              `deleteElement: MCP response for ${elementId}: ${mcpResponse.slice(0, 200)}`,
            );
            treeProvider.removeElement(elementId);
            deleted++;
            logEvent("OK", `deleteElement: removed ${elementId}`);
          } catch (e: any) {
            failed++;
            logEvent(
              "ERROR",
              `deleteElement: failed ${elementId}: ${e.message}`,
            );
          }
        }

        if (deleted > 0) {
          vscode.window.showInformationMessage(
            `Deleted: ${deleted} element(s)${failed > 0 ? ` (${failed} failed)` : ""}`,
          );
        } else {
          vscode.window.showErrorMessage(
            `Failed to delete ${failed} element(s)`,
          );
        }
        logEvent("INFO", `deleteElement: deleted=${deleted} failed=${failed}`);
      },
    ),
    vscode.commands.registerCommand("specEditor.setApiKey", async () => {
      const key: string | undefined = await vscode.window.showInputBox({
        prompt: "Enter your LLM API key (stored securely in OS keychain)",
        placeHolder: "sk-...",
        password: true,
      });
      if (key === undefined) return;
      await extensionContext.secrets.store("specEditor.apiKey", key);
      vscode.window.showInformationMessage("API key saved securely.");
      logEvent("INFO", "API key updated");
    }),
    vscode.commands.registerCommand("specEditor.runPending", async () => {
      vscode.commands.executeCommand("specEditor.run");
    }),
    vscode.commands.registerCommand("specEditor.reengineer", async () => {
      if (runActive) {
        vscode.window.showInformationMessage(
          "Spec Editor is already running. Use Stop button to cancel.",
        );
        return;
      }
      const wsRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!wsRoot) {
        vscode.window.showErrorMessage("No workspace folder open");
        return;
      }
      const py: string = detectedPythonPath || pythonPath;
      runTerminal = vscode.window.createTerminal({
        name: "Spec Editor Reengineer",
        hideFromUser: false,
        env: buildRunEnv(),
      });
      runActive = true;
      vscode.commands.executeCommand("setContext", "specEditor.runActive", true);
      statusBar.text = "$(sync~spin) Spec Editor — Reengineer";
      statusBar.command = "specEditor.stopRun";
      statusBar.tooltip = "Reengineer is running — click to stop";
      statusBar.color = new vscode.ThemeColor("statusBarItem.warningForeground");
      runTerminal.sendText(
        `cd "${wsRoot}" && ${py} -m src.main agent reengineer -p "${wsRoot}"`,
      );
      runTerminal.show();
      outputChannel.info(
        `[Reengineer] ${py} -m src.main agent reengineer -p "${wsRoot}"`,
      );
      logEvent("INFO", `specEditor.reengineer: started`);
    }),
    vscode.commands.registerCommand("specEditor.exportSpec", async () => {
      const wsRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!wsRoot) {
        vscode.window.showErrorMessage("No workspace folder open");
        return;
      }

      const formats: { label: string; description: string }[] = [
        { label: "srs", description: "IEEE 830 SRS document (Markdown)" },
        { label: "html", description: "Styled HTML with relationship diagrams" },
        { label: "trlc", description: "TRLC requirements-as-code (BMW-compatible)" },
        { label: "openapi", description: "OpenAPI 3.0 YAML (API-first methodology)" },
        { label: "jira", description: "Jira CSV for sprint backlog import" },
        { label: "compliance", description: "Compliance traceability matrix XLSX" },
      ];

      const format = await vscode.window.showQuickPick(formats, {
        placeHolder: "Select export format",
      });
      if (!format) return;

      const ext =
        format.label === "srs" ? "md" :
        format.label === "html" ? "html" :
        format.label === "trlc" ? "trlc" :
        format.label === "openapi" ? "yaml" :
        format.label === "jira" ? "csv" : "xlsx";
      const outFile = `${wsRoot}/export.${ext}`;

      // Resolve spec-editor binary: use the venv bin dir from detectedPythonPath
      const pyDir = path.dirname(detectedPythonPath || pythonPath);
      const specEditorBin = path.join(pyDir, "spec-editor");
      const cli = require("fs").existsSync(specEditorBin) ? specEditorBin : "spec-editor";

      const term = vscode.window.createTerminal({
        name: `Spec Editor Export (${format.label})`,
        hideFromUser: false,
        env: buildRunEnv(),
      });
      term.sendText(
        `${cli} export --format ${format.label} -p "${wsRoot}" -o "${outFile}"`,
      );
      term.show();
      outputChannel.info(
        `[Export] ${cli} export --format ${format.label} -p "${wsRoot}" -o "${outFile}"`,
      );
      logEvent(
        "INFO",
        `specEditor.export: format=${format.label} cli=${cli} output=${outFile}`,
      );

      // After a short delay, try to open the output file in editor
      setTimeout(async () => {
        try {
          if (require("fs").existsSync(outFile)) {
            const doc = await vscode.workspace.openTextDocument(
              vscode.Uri.file(outFile),
            );
            await vscode.window.showTextDocument(doc, { preview: false });
          }
        } catch {
          // File may not be ready yet — user can open manually
        }
      }, 3000);
    }),
    vscode.commands.registerCommand("specEditor.run", async () => {
      // Guard: if already running, ignore (use stopRun to stop)
      if (runActive) {
        vscode.window.showInformationMessage(
          "Spec Editor is already running. Use Stop button or status bar to cancel.",
        );
        return;
      }
      const wsRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!wsRoot) {
        vscode.window.showErrorMessage("No workspace folder open");
        return;
      }

      // Detect dev mode: wsRoot has src/__init__.py → use it as CWD
      // so python -m src.main can find the module.
      const srcInit: string = path.join(wsRoot, "src", "__init__.py");
      const cwd: string = require("fs").existsSync(srcInit)
        ? wsRoot
        : path.dirname(detectedPythonPath || pythonPath);

      const py: string = detectedPythonPath || pythonPath;
      runTerminal = vscode.window.createTerminal({
        name: "Spec Editor Run",
        hideFromUser: false,
        env: buildRunEnv(),
      });
      runActive = true;
      vscode.commands.executeCommand(
        "setContext",
        "specEditor.runActive",
        true,
      );
      statusBar.text = "$(sync~spin) Spec Editor — Running";
      statusBar.command = "specEditor.stopRun";
      statusBar.tooltip = "Spec Editor is running — click to stop";
      statusBar.color = new vscode.ThemeColor(
        "statusBarItem.warningForeground",
      );
      runTerminal.sendText(
        `cd "${cwd}" && ${py} -m src.main run -p "${wsRoot}"`,
      );
      runTerminal.show();
      outputChannel.info(
        `[Run] CMD: cd "${cwd}" && ${py} -m src.main run -p "${wsRoot}"`,
      );
      logEvent(
        "INFO",
        `specEditor.run: CMD: cd "${cwd}" && ${py} -m src.main run -p "${wsRoot}"`,
      );
      vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "Spec Editor — processing...",
          cancellable: true,
        },
        async (_progress, token) => {
          token.onCancellationRequested(() => {
            if (runTerminal) {
              runTerminal.sendText("");
            }
          });
          outputChannel.info(`Running spec-editor in ${wsRoot}...`);
          logEvent("INFO", `specEditor.run: STARTED in ${wsRoot}`);

          // Wait for Python process to finish.
          // Terminal stays open after completion (hideFromUser: false),
          // so use is_run_active (lock-file based) as the signal.
          // No skip logic — just poll until lock file is gone.

          let lastElementCount = treeProvider.elements.length;
          let lastTreeRefresh = 0;
          const TREE_REFRESH_INTERVAL = 5000; // refresh tree every 5s during run

          await new Promise<void>((resolve) => {
            const pollLock: NodeJS.Timeout = setInterval(async () => {
              const terminalDone =
                !runTerminal || runTerminal.exitStatus !== undefined;

              let mcpDone = false;
              let runMetrics: any = null;
              if (!terminalDone) {
                try {
                  const s: any = await callMcp("is_run_active", {});
                  const d: any = typeof s === "string" ? JSON.parse(s) : s;
                  mcpDone = !d?.active;
                  if (d?.active && d.elements != null) {
                    runMetrics = d;
                  }
                } catch {
                  try {
                    const s2: any = await callMcp("is_run_active", {});
                    const d2: any =
                      typeof s2 === "string" ? JSON.parse(s2) : s2;
                    mcpDone = !d2?.active;
                    if (d2?.active && d2.elements != null) {
                      runMetrics = d2;
                    }
                  } catch {
                    // Both attempts failed — rely on terminal check only
                  }
                }
              }

              // Update status bar with live metrics
              if (runMetrics && !mcpDone) {
                const el = runMetrics.elements || 0;
                const rel = runMetrics.relationships || 0;
                const ci = runMetrics.connectivity || 0;
                const orph = runMetrics.orphans || 0;
                statusBar.text = `$(sync~spin) Spec Editor — ${el} el, ${rel} rel | CI ${ci}`;
                statusBar.tooltip = `Running… ${el} elements, ${rel} relationships, CI=${ci}, ${orph} orphans`;

                // Incremental tree refresh: load new elements during run
                const now = Date.now();
                if (
                  el !== lastElementCount &&
                  now - lastTreeRefresh > TREE_REFRESH_INTERVAL
                ) {
                  lastElementCount = el;
                  lastTreeRefresh = now;
                  try {
                    const raw = await callMcp("list_all_elements", {});
                    const data = JSON.parse(raw);
                    const newElements = data.elements || [];
                    const oldIds = new Set(
                      treeProvider.elements.map((e: any) => e.id),
                    );
                    const added = newElements.filter(
                      (e: any) => !oldIds.has(e.id),
                    );
                    if (added.length > 0) {
                      treeProvider.elements = newElements;
                      treeProvider.refresh();
                      logEvent(
                        "TRACE",
                        `specEditor.run: tree updated +${added.length} elements (total ${newElements.length})`,
                      );
                      // Notify diagram viewer so it can auto-refresh
                      notifyWebView("elementsChanged");
                    }
                  } catch {
                    // MCP call failed — will retry next cycle
                  }
                }
              }

              if (terminalDone || mcpDone) {
                clearInterval(pollLock);
                runActive = false;
                runTerminal = undefined;
                vscode.commands.executeCommand(
                  "setContext",
                  "specEditor.runActive",
                  false,
                );
                statusBar.text = "$(checklist) Spec Editor";
                statusBar.command = "specEditor._quickOpen";
                statusBar.tooltip = "Spec Editor ready";
                statusBar.color = undefined;
                outputChannel.info("spec-editor run completed.");
                logEvent(
                  "INFO",
                  `specEditor.run: COMPLETED (terminal=${terminalDone} mcp=${mcpDone})`,
                );
                treeProvider.loadElements();
                resolve();
              }
            }, 3000); // Poll every 3s
          });
        },
      );
    }),
    vscode.commands.registerCommand("specEditor.stopRun", async () => {
      if (!runActive || !runTerminal) {
        vscode.window.showInformationMessage("No run in progress.");
        return;
      }
      runTerminal.sendText("");
      // Also dispose the terminal to force-kill the process
      runTerminal.dispose();
      runActive = false;
      runTerminal = undefined;
      vscode.commands.executeCommand(
        "setContext",
        "specEditor.runActive",
        false,
      );
      statusBar.text = "$(checklist) Spec Editor";
      statusBar.command = "specEditor._quickOpen";
      statusBar.tooltip = "Spec Editor ready";
      statusBar.color = undefined;
      outputChannel.info("spec-editor run stopped.");
      logEvent("INFO", "specEditor.run: STOPPED by stopRun");
    }),
    vscode.commands.registerCommand("specEditor.restartMcp", async () => {
      logEvent("INFO", "restartMcp triggered");
      if (mcpProcess) {
        mcpProcess.kill();
        mcpProcess = undefined;
      }
      mcpConnected = false;
      statusBar.text = "$(sync~spin) Spec Editor";
      logEvent("INFO", "StatusBar set: $(sync~spin) (restartMcp)");
      statusBar.tooltip = "Restarting MCP server...";
      const cfg: vscode.WorkspaceConfiguration =
        vscode.workspace.getConfiguration("specEditor");
      try {
        await startMcpServer(cfg);
      } catch (err: any) {
        vscode.window.showErrorMessage(`MCP restart failed: ${err.message}`);
      }
    }),
    vscode.commands.registerCommand("specEditor._quickOpen", async () => {
      await mcpReady;
      const last: string | undefined =
        extensionContext.workspaceState.get("lastProject");
      const wsRoot: string | undefined =
        vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      const projPath: string =
        last || wsRoot || "";
      if (!projPath) {
        logEvent("WARN", "_quickOpen: no workspace folder and no last project");
        return;
      }
      logEvent("INFO", `_quickOpen: trying ${projPath}`);
      if (!require("fs").existsSync(path.join(projPath, "methodology.yaml"))) {
        logEvent("WARN", `_quickOpen: no methodology.yaml in ${projPath}`);
        vscode.window.showErrorMessage("No methodology.yaml in " + projPath);
        return;
      }
      logEvent("INFO", "_quickOpen: switching project");
      // Multi-project: update workspace folders to match new project
      if (vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders.length > 0) {
        const firstFolder = vscode.workspace.workspaceFolders[0];
        // Don't call switch_project — just update the workspace
        await vscode.commands.executeCommand("vscode.openFolder", firstFolder.uri, { forceNewWindow: true });
      }
      logEvent("INFO", "_quickOpen: loading elements");
      treeProvider.loadElements();
      notifyWebView("projectLoaded");
      logEvent("INFO", "_quickOpen: loading elements");
      treeProvider.loadElements();
      notifyWebView("projectLoaded");
      vscode.commands.executeCommand(
        "setContext",
        "specEditor.projectLoaded",
        true,
      );
      mcpConnected = true;
      mcpStatusTooltip = `MCP server on port ${activeMcpPort || mcpPort}`;
      statusBar.text = "$(checklist) Spec Editor";
      statusBar.tooltip = mcpStatusTooltip;
      statusBar.color = undefined;
      statusBar.command = "specEditor.showLog";
      logEvent("OK", "_quickOpen: status bar $(checklist)");
      setTimeout(connectSSE, 1000);
    }),
    vscode.commands.registerCommand("specEditor.openSettings", () =>
      vscode.commands.executeCommand(
        "workbench.action.openSettings",
        "@ext:spec-editor.spec-editor-vscode",
      ),
    ),
    vscode.commands.registerCommand("specEditor._diagnostics", () => {
      const diag: any = {
        timestamp: new Date().toISOString(),
        mcpPort,
        activeMcpPort,
        pythonPath,
        mcpProcessRunning:
          mcpProcess !== undefined && mcpProcess.exitCode === null,
        mcpProcessExitCode: mcpProcess?.exitCode ?? null,
        mcpConnected,
        mcpStatusTooltip,
        statusBarText: statusBar.text,
        statusBarTooltip: statusBar.tooltip,
        statusBarColor: String(statusBar.color ?? "none"),
        statusBarCommand: statusBar.command,
        detectedPythonPath: detectedPythonPath || pythonPath,
        detectionTrace,
      };
      outputChannel.info(`[DIAGNOSTICS] ${JSON.stringify(diag, null, 2)}`);
      return diag;
    }),
    vscode.commands.registerCommand("specEditor._getStatus", () => ({
      text: statusBar.text,
      tooltip: statusBar.tooltip,
      color: statusBar.color,
      command: statusBar.command,
      mcpProcessRunning:
        mcpProcess !== undefined && mcpProcess.exitCode === null,
      mcpConnected,
      mcpStatusTooltip,
      treeMessage: treeView.message,
      elementCount: treeProvider.elements.length,
      projectPath: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || null,
    })),
  );

  treeProvider = new SpecTreeProvider();
  treeView = vscode.window.createTreeView("specEditor.treeView", {
    treeDataProvider: treeProvider,
    dragAndDropController: new SpecDragDropController(),
    canSelectMany: true,
  });
  treeView.message = "Loading…";
  context.subscriptions.push(treeView);

  treeView.onDidExpandElement(
    (e: vscode.TreeViewExpansionEvent<SpecTreeItem>) => {
      const aspect: string | undefined = e.element.context?.aspect;
      if (aspect) {
        treeProvider._expandedAspects.add(aspect);
        treeProvider._saveExpandedState();
        logEvent("TRACE", `tree expanded: ${aspect}`);
      }
    },
  );

  treeView.onDidCollapseElement(
    (e: vscode.TreeViewExpansionEvent<SpecTreeItem>) => {
      const aspect: string | undefined = e.element.context?.aspect;
      if (aspect) {
        treeProvider._expandedAspects.delete(aspect);
        treeProvider._saveExpandedState();
        logEvent("TRACE", `tree collapsed: ${aspect}`);
      }
    },
  );

  const savedExpanded: string[] | undefined = context.workspaceState.get(
    "specEditor.expandedAspects",
  );
  if (savedExpanded && savedExpanded.length > 0) {
    treeProvider._expandedAspects = new Set(savedExpanded);
  }

  // SSE connection for real-time updates
  function connectSSE(): void {
    const port = activeMcpPort || mcpPort;
    const url = `http://127.0.0.1:${port}/events`;

    logEvent("INFO", `SSE: connecting to ${url}`);

    // Use Node.js http module to connect to SSE
    const http = require("http");
    const req = http.get(url, (res: any) => {
      logEvent("OK", `SSE: connected (status ${res.statusCode})`);
      let buffer = "";

      res.on("data", (chunk: Buffer) => {
        buffer += chunk.toString();
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = "";
        let eventData = "";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            eventData = line.slice(6).trim();
          } else if (line === "" && eventType && eventData) {
            // Complete event received
            try {
              const data = JSON.parse(eventData);
              logEvent("TRACE", `SSE: ${eventType} ${JSON.stringify(data)}`);

              if (
                eventType === "element_updated" ||
                eventType === "relationship_updated"
              ) {
                // Debounced tree refresh for live spec updates
                if (sseDebounceTimer) clearTimeout(sseDebounceTimer);
                sseDebounceTimer = setTimeout(() => {
                  treeView.message = "Live spec update received…";
                  treeProvider.loadElements();
                  setTimeout(() => {
                    treeView.message = undefined;
                  }, 1500);
                }, 1000);
              } else if (eventType === "project_switched") {
                // Immediate full tree reload on project switch
                logEvent(
                  "INFO",
                  `SSE: project switched to ${data.project}, reloading tree`,
                );
                treeProvider.loadElements();
              }
            } catch (e: any) {
              logEvent("WARN", `SSE: parse error ${e.message}`);
            }
            eventType = "";
            eventData = "";
          }
        }
      });

      res.on("error", (err: Error) => {
        logEvent("WARN", `SSE: stream error ${err.message}`);
      });

      res.on("end", () => {
        logEvent("WARN", "SSE: connection closed, reconnecting in 5s...");
        sseReconnectTimer = setTimeout(connectSSE, 5000);
      });
    });

    req.on("error", (err: Error) => {
      logEvent("WARN", `SSE: connect failed ${err.message}, retrying in 5s...`);
      sseReconnectTimer = setTimeout(connectSSE, 5000);
    });

    req.end();
  }

  const config: vscode.WorkspaceConfiguration =
    vscode.workspace.getConfiguration("specEditor");
  if (config.get<boolean>("autoStartMcp", true)) {
    mcpReady = startMcpServer(config).catch((err: Error) =>
      outputChannel.error(`Failed to start MCP server: ${err.message}`),
    );
  }

  // After MCP server starts, connect SSE for live updates
  mcpReady.then(() => {
    if (mcpConnected) {
      setTimeout(connectSSE, 2000); // Wait 2s for server to be fully ready
    } else {
      logEvent("WARN", "Skipping SSE connect because MCP is not connected");
    }
  });

  // ── Register LM tools: expose spec-editor MCP tools to Copilot directly ──
  _registerLmTools(context);
}

// ═══════════════════════════════════════════════════════════════════════════
// LM Tool registry — exposes MCP tools as vscode.lm registered tools
// ═══════════════════════════════════════════════════════════════════════════

/** Wrap an MCP tool call as a VS Code Language Model Tool. */
class McpLmTool implements vscode.LanguageModelTool<any> {
  constructor(
    private mcpMethod: string,
    private description: string,
    private inputSchema: any,
  ) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<any>,
    _token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelToolResult> {
    try {
      const result: string = await callMcp(this.mcpMethod, options.input);
      const data: any = JSON.parse(result);
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(
          JSON.stringify(data, null, 2).slice(0, 8000),
        ),
      ]);
    } catch (err: any) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(`Error: ${err.message}`),
      ]);
    }
  }
}

function _registerLmTools(context: vscode.ExtensionContext): void {
  // Check if vscode.lm API is available (requires VS Code 1.94+)
  if (!(vscode as any).lm || !(vscode as any).lm.registerTool) {
    logEvent("WARN", "vscode.lm.registerTool not available — LM tools skipped");
    outputChannel.warn("vscode.lm API not available. Upgrade VS Code to 1.94+ for AI tool integration.");
    return;
  }

  const tools: Array<{
    id: string;
    method: string;
    description: string;
    schema: any;
  }> = [
    {
      id: "spec-editor_read_element",
      method: "read_element",
      description:
        "Read a specification element by ID. Returns aspect, type, title, status, parent, children, relationships, and content. Use deep=true to also get resolved children and related elements.",
      schema: {
        type: "object",
        properties: {
          element_id: { type: "string", description: "Element ID (e.g. MOD-001)" },
          deep: { type: "boolean", description: "Include resolved children and related elements" },
        },
        required: ["element_id"],
      },
    },
    {
      id: "spec-editor_search_elements",
      method: "search_elements",
      description:
        "Full-text search across specification elements by ID, title, and content.",
      schema: {
        type: "object",
        properties: {
          query: { type: "string", description: "Search query" },
        },
        required: ["query"],
      },
    },
    {
      id: "spec-editor_get_file_tree",
      method: "get_file_tree",
      description:
        "List the project file structure. Returns sorted list of all files, skipping node_modules, .git, etc.",
      schema: {
        type: "object",
        properties: {
          path: { type: "string", description: "Subdirectory path (default: project root)" },
        },
      },
    },
    {
      id: "spec-editor_search_code",
      method: "search_code",
      description:
        "Search for a pattern in code files. Uses grep with include filters for common source file types.",
      schema: {
        type: "object",
        properties: {
          pattern: { type: "string", description: "Text or regex pattern to search for" },
          path: { type: "string", description: "Subdirectory to search (default: project root)" },
        },
        required: ["pattern"],
      },
    },
    {
      id: "spec-editor_find_related",
      method: "find_related",
      description:
        "Find all elements related to the specified element (parent, children, relationship targets).",
      schema: {
        type: "object",
        properties: {
          element_id: { type: "string", description: "Element ID" },
        },
        required: ["element_id"],
      },
    },
    {
      id: "spec-editor_search_symbol",
      method: "search_symbol",
      description:
        "Search for code symbols (classes, functions, methods) by name. Uses language-specific parsers (Python, TypeScript, Go, Java, Rust). Returns symbol name, kind, file, line, decorators, and docstring.",
      schema: {
        type: "object",
        properties: {
          code_dir: { type: "string", description: "Path to code directory" },
          query: { type: "string", description: "Symbol name to search for (partial match)" },
        },
        required: ["code_dir", "query"],
      },
    },
  ];

  try {
    for (const t of tools) {
      const tool = new McpLmTool(t.method, t.description, t.schema);
      context.subscriptions.push(
        (vscode as any).lm.registerTool(t.id, tool),
      );
      logEvent("INFO", `LM tool registered: ${t.id}`);
    }
    outputChannel.info(`Registered ${tools.length} LM tools for Copilot`);
  } catch (err: any) {
    logEvent("ERROR", `LM tool registration failed: ${err.message}`);
    outputChannel.warn(`LM tool registration failed: ${err.message}`);
  }
}

export function deactivate(): void {
  vscode.commands.executeCommand(
    "setContext",
    "specEditor.projectLoaded",
    false,
  );
  if (sseReconnectTimer) clearTimeout(sseReconnectTimer);
  if (sseDebounceTimer) clearTimeout(sseDebounceTimer);
  if (mcpProcess) {
    mcpProcess.kill();
    mcpProcess = undefined;
  }
  outputChannel.info("Spec Editor extension deactivated");
}

async function detectPython(
  config: vscode.WorkspaceConfiguration,
): Promise<string> {
  const configured: string = config.get<string>("pythonPath", "");
  const candidates: string[] = [];
  if (configured) {
    candidates.push(configured);
  }

  const workspaceRoot: string | undefined =
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const home: string = require("os").homedir();

  if (workspaceRoot) {
    candidates.push(
      path.join(workspaceRoot, ".venv", "bin", "python"),
      path.join(workspaceRoot, ".venv", "bin", "python3"),
    );
    const srcInit: string = path.join(workspaceRoot, "src", "__init__.py");
    if (require("fs").existsSync(srcInit)) {
      candidates.push(
        path.join(workspaceRoot, ".venv", "bin", "python"),
        path.join(workspaceRoot, ".venv", "bin", "python3"),
      );
    }
  }

  try {
    const { stdout } = await execCommand(
      "/bin/sh",
      ["-lc", "which python3 2>/dev/null || echo ''"],
      3000,
    );
    const shellPy: string = stdout.trim();
    if (shellPy && !candidates.includes(shellPy)) {
      candidates.push(shellPy);
    }
  } catch {
    // ignore
  }

  candidates.push(
    path.join(home, ".local", "bin", "python3"),
    path.join(home, ".local", "bin", "python"),
  );

  const devPaths: string[] = [
    path.join(
      home,
      "Documents",
      "Droid",
      "spec-editor2",
      ".venv",
      "bin",
      "python",
    ),
    path.join(
      home,
      "Documents",
      "Droid",
      "spec-editor2",
      ".venv",
      "bin",
      "python3",
    ),
    path.join(home, "spec-editor2", ".venv", "bin", "python"),
    path.join(home, "spec-editor2", ".venv", "bin", "python3"),
  ];
  for (const dp of devPaths) {
    if (require("fs").existsSync(dp) && !candidates.includes(dp)) {
      candidates.push(dp);
    }
  }

  if (!configured) {
    candidates.push("python3", "python");
  }

  outputChannel.info(
    `detectPython: ${candidates.length} candidates: ${candidates.slice(0, 5).join(", ")}...`,
  );

  for (const candidate of candidates) {
    try {
      const { stdout } = await execCommand(
        candidate,
        ["-c", "import sys; print(sys.executable)"],
        3000,
      );
      const found: string = stdout.trim();
      if (found) {
        try {
          await execCommand(found, ["--version"], 3000);
          detectionTrace.push(`DETECT: ${found}`);
          outputChannel.info(`Python detected: ${found}`);
          detectedPythonPath = found;
          return found;
        } catch {
          // try next
        }
      }
    } catch {
      // try next
    }
  }

  return configured || "python3";
}

function buildRunEnv(): { [key: string]: string } {
  // Build env vars for the Run terminal, same as MCP server gets.
  const env: { [key: string]: string } = { ...process.env } as any;

  // API key from VSCode secrets
  try {
    const apiKey: string | undefined = (
      require("vscode") as typeof vscode
    ).workspace
      .getConfiguration("specEditor")
      .get("apiKey");
    if (apiKey) {
      env["LLM_API_KEY"] = apiKey;
      env["DEEPSEEK_API_KEY"] = apiKey;
      env["OPENAI_API_KEY"] = apiKey;
      env["ANTHROPIC_API_KEY"] = apiKey;
    }
  } catch {}

  // Agent config from VSCode settings
  const cfg = vscode.workspace.getConfiguration("specEditor");
  const agents: string[] = ["reasoningModel", "chatModel", "orchestrator"];
  for (const a of agents) {
    const pfx: string =
      a === "orchestrator"
        ? "SPEC_EDITOR__ORCHESTRATOR"
        : a === "reasoningModel"
          ? "SPEC_EDITOR__AGENT_1"
          : "SPEC_EDITOR__AGENT_2";
    const provider: string | undefined = cfg.get(`${a}.provider`);
    const model: string | undefined = cfg.get(`${a}.model`);
    const temp: number | undefined = cfg.get(`${a}.temperature`);
    const maxTokens: number | undefined = cfg.get(`${a}.maxTokens`);
    if (provider) env[`${pfx}__PROVIDER`] = provider;
    if (model) env[`${pfx}__MODEL`] = model;
    if (temp !== undefined) env[`${pfx}__TEMPERATURE`] = String(temp);
    if (maxTokens !== undefined) env[`${pfx}__MAX_TOKENS`] = String(maxTokens);
  }

  return env;
}

async function isPortFree(port: number): Promise<boolean> {
  const net = require("net");
  const hosts = ["127.0.0.1", "::1"];

  for (const host of hosts) {
    const free: boolean = await new Promise<boolean>((resolve) => {
      const socket = new net.Socket();
      let settled = false;

      const finish = (value: boolean): void => {
        if (settled) return;
        settled = true;
        socket.destroy();
        resolve(value);
      };

      socket.setTimeout(500);
      socket.once("connect", () => finish(false));
      socket.once("timeout", () => finish(true));
      socket.once("error", (err: any) => {
        if (err && err.code === "ECONNREFUSED") {
          finish(true);
        } else if (
          err &&
          (err.code === "EADDRNOTAVAIL" || err.code === "EHOSTUNREACH")
        ) {
          finish(true);
        } else {
          finish(false);
        }
      });
      try {
        socket.connect(port, host);
      } catch {
        finish(true);
      }
    });
    if (!free) return false;
  }
  return true;
}

async function startMcpServer(
  config: vscode.WorkspaceConfiguration,
): Promise<void> {
  const workspaceRoot: string | undefined =
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  mcpPort = config.get<number>("mcpPort", 8088);
  const mcpMode: string = config.get<string>("mcpMode", "local");

  // ── Auto-detect: if port 8088 already has a working MCP, use it ──
  // Works for Docker MCP, pre-started MCP, or any external MCP server.
  if (mcpMode !== "local") {
    // skip auto-detect only if explicitly set to local
    // nothing to skip
  }
  {
    try {
      await callMcpRaw("initialize", {});
      mcpConnected = true;
      activeMcpPort = mcpPort;
      mcpStatusTooltip = `MCP server on port ${mcpPort} (auto-detected)`;
      statusBar.text = "$(checklist) Spec Editor";
      statusBar.tooltip = mcpStatusTooltip;
      statusBar.color = undefined;
      logEvent("OK", `Auto-detected MCP on port ${mcpPort}`);

      // Restore project: try lastProject, then findProject, then wsRoot
      const lastProj: string | undefined =
        extensionContext.workspaceState.get("lastProject");
      const wsRoot: string | undefined =
        vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;

      // Try last saved project, then scan workspace folders for methodology.yaml
      let localPath: string | undefined =
        lastProj ?? (await findProject()) ?? undefined;

      // Fallback: check workspace root directly
      if (!localPath && wsRoot) {
        try {
          if (require("fs").existsSync(path.join(wsRoot, "methodology.yaml"))) {
            localPath = wsRoot;
          }
        } catch {
          /* ignore */
        }
      }

      // Fallback: if wsRoot is a project dir, try Docker mapping by name
      if (!localPath && wsRoot) {
        const folderName = path.basename(wsRoot);
        const dockerFallback = `/projects/${folderName}`;
        logEvent(
          "INFO",
          `Auto-detect: trying Docker fallback ${dockerFallback}`,
        );
        try {
          // Multi-project: project_path is auto-injected by callMcp.
          // Just verify the project is reachable by listing elements.
          await callMcp("list_all_elements", { project_path: dockerFallback });
          logEvent(
            "OK",
            `Auto-detect: Docker fallback ${dockerFallback} is reachable`,
          );
          treeProvider.loadElements();
          return;
        } catch {
          logEvent(
            "WARN",
            `Auto-detect: Docker fallback failed for ${dockerFallback}`,
          );
        }
      }

      logEvent(
        "INFO",
        `Auto-detect: lastProj=${lastProj || "null"} wsRoot=${wsRoot || "null"} localPath=${localPath || "null"}`,
      );

      if (localPath) {
        // Try Docker-mapped path first (volume: ~/Documents/Droid → /projects)
        const homeDir: string = require("os").homedir();
        const droidPrefix: string = path.join(homeDir, "Documents", "Droid");
        let dockerPath: string | undefined;
        if (localPath.startsWith(droidPrefix)) {
          dockerPath = "/projects" + localPath.substring(droidPrefix.length);
        }

        let switched = false;
        for (const p of [dockerPath, localPath]) {
          if (!p) continue;
          try {
            // Multi-project: verify project reachable by listing elements
            await callMcp("list_all_elements", { project_path: p });
            logEvent("OK", `Auto-detect: project ${p} is reachable`);
            switched = true;
            break;
          } catch {
            logEvent("WARN", `Auto-detect: project ${p} not reachable`);
          }
        }
        if (!switched) {
          logEvent("WARN", "Auto-detect: could not switch project");
        }
      }

      treeProvider.loadElements();
      return;
    } catch {
      // No MCP on port 8088 — proceed with local spawn
    }
  }

  if (mcpMode === "docker") {
    outputChannel.info(`[Docker mode] Health-checking port ${mcpPort}...`);
    logEvent("INFO", `Docker mode: checking port ${mcpPort}`);
    for (let i = 0; i < UI_DELAY.MCP_READY_CYCLES; i++) {
      try {
        await callMcpRaw("initialize", {});
        mcpConnected = true;
        mcpStatusTooltip = `Docker MCP on port ${mcpPort}`;
        activeMcpPort = mcpPort;
        statusBar.text = "$(checklist) Spec Editor";
        statusBar.tooltip = mcpStatusTooltip;
        statusBar.color = undefined;
        outputChannel.info(`[Docker mode] MCP available on port ${mcpPort}`);
        logEvent("OK", `Docker MCP connected on port ${mcpPort}`);
        treeProvider.loadElements();
        return;
      } catch {
        await new Promise<void>((r) => setTimeout(r, UI_DELAY.DOCKER_CHECK));
      }
    }
    outputChannel.warn(`[Docker mode] MCP not reachable on port ${mcpPort}`);
    logEvent("ERROR", `Docker MCP not reachable on port ${mcpPort}`);
    statusBar.text = "$(error) Spec Editor";
    statusBar.tooltip = `Docker MCP not found on port ${mcpPort}`;
    return;
  }

  let pythonPath2: string = await detectPython(config);
  detectedPythonPath = pythonPath2;
  const importCode: string =
    "from src.mcp.server import MCPHandler, run_http_server; print('ok')";
  let specEditorInstalled: boolean = false;

  const tryImportOn = async (py: string, cwd?: string): Promise<boolean> => {
    try {
      const result = await execCommand(py, ["-c", importCode], 5000, cwd);
      if (result.stdout.trim() === "ok") {
        outputChannel.info(
          `spec-editor found: ${py}${cwd ? " (dev mode)" : ""}`,
        );
        return true;
      }
    } catch {
      // not found
    }
    return false;
  };

  if (workspaceRoot) {
    const srcInit: string = path.join(workspaceRoot, "src", "__init__.py");
    if (require("fs").existsSync(srcInit)) {
      if (await tryImportOn(pythonPath2, workspaceRoot)) {
        specEditorInstalled = true;
      }
    }
  }

  if (!specEditorInstalled) {
    if (await tryImportOn(pythonPath2)) {
      specEditorInstalled = true;
    }
  }

  if (!specEditorInstalled) {
    logEvent(
      "WARN",
      `RETRY: spec-editor not in ${pythonPath2} — trying alternatives`,
    );
    detectionTrace.push(
      `RETRY: spec-editor not in ${pythonPath2}, trying alternatives...`,
    );
    logEvent(
      "WARN",
      `spec-editor not found in detected Python (${pythonPath2}). Trying alternatives...`,
    );

    const altPythons: string[] = [];
    const home: string = require("os").homedir();

    if (workspaceRoot) {
      altPythons.push(
        path.join(workspaceRoot, ".venv", "bin", "python"),
        path.join(workspaceRoot, ".venv", "bin", "python3"),
      );
    }

    try {
      const { stdout } = await execCommand(
        "/bin/sh",
        [
          "-lc",
          "which python3 2>/dev/null || which python 2>/dev/null || echo ''",
        ],
        3000,
      );
      const sh: string = stdout.trim();
      if (sh) altPythons.push(sh);
    } catch {
      // ignore
    }

    const devPaths: string[] = [
      path.join(
        home,
        "Documents",
        "Droid",
        "spec-editor2",
        ".venv",
        "bin",
        "python",
      ),
      path.join(
        home,
        "Documents",
        "Droid",
        "spec-editor2",
        ".venv",
        "bin",
        "python3",
      ),
      path.join(home, "spec-editor2", ".venv", "bin", "python"),
      path.join(home, "spec-editor2", ".venv", "bin", "python3"),
    ];
    for (const dp of devPaths) {
      if (require("fs").existsSync(dp)) altPythons.push(dp);
    }
    altPythons.push(
      path.join(home, ".local", "bin", "python3"),
      path.join(home, ".local", "bin", "python"),
    );

    for (const alt of altPythons) {
      if (alt === pythonPath2) continue;
      logEvent("INFO", `TRY: ${alt}`);
      detectionTrace.push(`TRY: ${alt}`);
      outputChannel.info(`Trying alternative Python: ${alt}`);
      if (await tryImportOn(alt)) {
        logEvent("OK", `FOUND spec-editor in ${alt}`);
        detectionTrace.push(`FOUND: spec-editor in ${alt}`);
        pythonPath2 = alt;
        detectedPythonPath = alt;
        specEditorInstalled = true;
        break;
      }
    }
  }

  if (!specEditorInstalled) {
    const installCmd: string = `${pythonPath2} -m pip install spec-editor`;
    logEvent("WARN", `[startMcp] spec-editor NOT FOUND for: ${pythonPath2}`);
    logEvent("ERROR", `spec-editor NOT FOUND for: ${pythonPath2}`);
    const msg: string = `spec-editor Python module not found for: ${pythonPath2}`;
    const choice: string | undefined = await vscode.window.showErrorMessage(
      msg,
      { modal: false },
      "Configure Python path...",
      "Install spec-editor",
      "Later",
    );
    if (choice === "Configure Python path...") {
      vscode.commands.executeCommand(
        "workbench.action.openSettings",
        "@ext:spec-editor.spec-editor-vscode",
      );
      outputChannel.info(
        `User chose to configure pythonPath. Detected: ${pythonPath2}`,
      );
    } else if (choice === "Install spec-editor") {
      const terminal: vscode.Terminal = vscode.window.createTerminal(
        "Spec Editor Install",
      );
      terminal.sendText(installCmd);
      terminal.show();
      outputChannel.info(`User chose to install: ${installCmd}`);
    }
    logEvent(
      "WARN",
      `[MCP] Set tooltip: Not connected — ${pythonPath2} has no spec-editor`,
    );
    statusBar.tooltip = `Not connected — ${pythonPath2} has no spec-editor. Click gear to configure.`;
    return;
  }

  let projectPath: string | undefined = await findProject();
  if (!projectPath && workspaceRoot) {
    const initMarker: string = path.join(workspaceRoot, "methodology.yaml");
    if (!require("fs").existsSync(initMarker)) {
      logEvent("INFO", `Auto-init spec-editor project in ${workspaceRoot}`);
      outputChannel.info(
        `Initialising spec-editor project in ${workspaceRoot}...`,
      );
      try {
        const template: string = getMethodologyTemplate("waterfall");
        require("fs").writeFileSync(initMarker, template);
        require("fs").mkdirSync(path.join(workspaceRoot, "aspects"), {
          recursive: true,
        });
        outputChannel.info("Project initialised.");
        projectPath = workspaceRoot;
      } catch (e: any) {
        outputChannel.warn(`Auto-init failed: ${e.message}`);
      }
    }
  }

  if (!projectPath) {
    logEvent("WARN", "No methodology.yaml found in workspace");
    outputChannel.warn(
      "No methodology.yaml found in workspace — MCP starts without project",
    );
  } else {
    logEvent("INFO", `Project found: ${projectPath}`);
  }

  let actualPort: number = mcpPort;
  for (let attempt = 0; attempt < 10; attempt++) {
    const free: boolean = await isPortFree(actualPort);
    if (free) break;
    outputChannel.info(`Port ${actualPort} busy, trying ${actualPort + 1}...`);
    actualPort++;
  }

  let restartCount: number = 0;
  const MAX_RESTARTS: number = 3;

  const spawnMcp = async (): Promise<void> => {
    const serverCode: string = projectPath
      ? `from pathlib import Path; from src.mcp.server import MCPHandler, run_http_server; handler = MCPHandler(project_path=Path("${projectPath.replace(/"/g, '\\"')}"), writable=True); run_http_server(handler, '127.0.0.1', ${actualPort})`
      : `from src.mcp.server import MCPHandler, run_http_server; handler = MCPHandler(project_path=None, writable=True); run_http_server(handler, '127.0.0.1', ${actualPort})`;

    const envOverrides: Record<string, string> = {};
    try {
      const apiKey: string | undefined =
        await extensionContext.secrets.get("specEditor.apiKey");
      if (apiKey) {
        // Set both generic and provider-specific env vars.
        // LiteLLM auto-detects from DEEPSEEK_API_KEY, OPENAI_API_KEY, etc.
        envOverrides["LLM_API_KEY"] = apiKey;
        envOverrides["DEEPSEEK_API_KEY"] = apiKey;
        envOverrides["OPENAI_API_KEY"] = apiKey;
        envOverrides["ANTHROPIC_API_KEY"] = apiKey;
      }
    } catch {
      // ignore
    }

    const cfg: vscode.WorkspaceConfiguration =
      vscode.workspace.getConfiguration("specEditor");

    // Pass restrictSourceDeletion setting to MCP server
    const restrictSrc: boolean = cfg.get<boolean>(
      "restrictSourceDeletion",
      true,
    );
    envOverrides["SPEC_EDITOR__RESTRICT_SOURCE_DELETION"] = String(restrictSrc);

    const agents: string[] = ["reasoningModel", "chatModel", "orchestrator"];
    for (const a of agents) {
      const pfx: string =
        a === "orchestrator"
          ? "SPEC_EDITOR__ORCHESTRATOR"
          : a === "reasoningModel"
            ? "SPEC_EDITOR__AGENT_1"
            : "SPEC_EDITOR__AGENT_2";
      const provider: string | undefined = cfg.get(`${a}.provider`);
      const model: string | undefined = cfg.get(`${a}.model`);
      const temp: number | undefined = cfg.get(`${a}.temperature`);
      const maxTokens: number | undefined = cfg.get(`${a}.maxTokens`);
      if (provider) envOverrides[`${pfx}__PROVIDER`] = provider;
      if (model) envOverrides[`${pfx}__MODEL`] = model;
      if (temp !== undefined)
        envOverrides[`${pfx}__TEMPERATURE`] = String(temp);
      if (maxTokens !== undefined)
        envOverrides[`${pfx}__MAX_TOKENS`] = String(maxTokens);
    }

    // Determine the spec-editor2 source repo root.
    // Priority 1: extension-relative path (dev mode — source lives next to extension).
    let repoRoot: string = path.resolve(extensionContext.extensionPath, "..", "..");
    let localServerPath: string = path.join(repoRoot, "src", "mcp", "server.py");

    // Priority 2: derive from detected pythonPath (e.g. .../spec-editor2/.venv/bin/python).
    // This is needed when the extension is installed from .vsix and the source
    // lives in a separate repo. Without this, PYTHONPATH is not set and the MCP
    // server may pick up conflicting modules from the workspace cwd (e.g. a
    // workspace that also has src/storage.py would shadow spec-editor2's src/storage/).
    if (!require("fs").existsSync(localServerPath)) {
      const pythonDir: string = path.dirname(pythonPath2);
      // Walk up from the Python binary looking for src/mcp/server.py
      let candidate: string = pythonDir;
      for (let i = 0; i < 6; i++) {
        const candidateServer: string = path.join(candidate, "src", "mcp", "server.py");
        if (require("fs").existsSync(candidateServer)) {
          repoRoot = candidate;
          localServerPath = candidateServer;
          logEvent("INFO", `[spawnMcp] Found spec-editor2 repo via pythonPath: ${repoRoot}`);
          break;
        }
        candidate = path.dirname(candidate);
      }
    }

    const spawnCwd: string = require("fs").existsSync(localServerPath)
      ? repoRoot
      : workspaceRoot || process.cwd();

    // ALWAYS set PYTHONPATH when we found the repo — otherwise cwd modules
    // (from the workspace project) can shadow spec-editor2 source packages.
    if (require("fs").existsSync(localServerPath)) {
      envOverrides["PYTHONPATH"] = repoRoot;
    }

    logEvent(
      "INFO",
      `Spawning MCP using python=${pythonPath2} cwd=${spawnCwd} port=${actualPort}`,
    );
    if (envOverrides["PYTHONPATH"]) {
      logEvent("INFO", `PYTHONPATH=${envOverrides["PYTHONPATH"]}`);
    }

    mcpProcess = spawn(pythonPath2, ["-c", serverCode], {
      stdio: ["pipe", "pipe", "pipe"],
      cwd: spawnCwd,
      env: { ...process.env, ...envOverrides },
    });

    mcpProcess.stdout?.on("data", (d: Buffer) => {
      const text = d.toString().trim();
      if (text) {
        logEvent("INFO", `[MCP stdout] ${text}`);
        outputChannel.debug(`[MCP] ${text}`);
      }
    });
    mcpProcess.stderr?.on("data", (d: Buffer) => {
      const text = d.toString().trim();
      if (text) {
        logEvent("ERROR", `[MCP stderr] ${text}`);
        outputChannel.error(`[MCP] ${text}`);
      }
    });

    mcpProcess.on("close", (code: number | null) => {
      logEvent(
        "WARN",
        `[MCP] EXITED code=${code} restarts=${restartCount}/${MAX_RESTARTS}`,
      );
      mcpProcess = undefined;
      if (code !== 0 && code !== null && restartCount < MAX_RESTARTS) {
        restartCount++;
        outputChannel.info(`MCP restart ${restartCount}/${MAX_RESTARTS}...`);
        setTimeout(spawnMcp, UI_DELAY.MCP_RESTART);
      } else {
        logEvent("WARN", "StatusBar set: $(error) (MCP exited)");
        mcpConnected = false;
        mcpStatusTooltip =
          restartCount >= MAX_RESTARTS
            ? "MCP crashed repeatedly"
            : "MCP server disconnected";
        statusBar.text = "$(error) Spec Editor";
        statusBar.command = "specEditor._quickOpen";
        statusBar.tooltip = mcpStatusTooltip;
        statusBar.color = new vscode.ThemeColor(
          "statusBarItem.errorForeground",
        );
        outputChannel.error(
          `MCP stopped (restarts=${restartCount}, code=${code})`,
        );
      }
    });

    activeMcpPort = actualPort;
    outputChannel.info(`[MCP] launched on http://127.0.0.1:${actualPort}/mcp`);
  };

  spawnMcp();

  await new Promise<void>((r) => setTimeout(r, UI_DELAY.PROJECT_SWITCH));

  let mcpInitialized = false;
  for (let i = 0; i < UI_DELAY.MCP_READY_CYCLES; i++) {
    try {
      await callMcpRaw("initialize", {});
      await treeProvider.loadElements();
      logEvent("OK", "Tree elements loaded");
      if (treeView.message === "Loading…")
        treeView.message = "No project open — use Open Project";
      mcpConnected = true;
      mcpStatusTooltip = `MCP server on port ${actualPort}`;
      activeMcpPort = actualPort;
      statusBar.text = projectPath
        ? "$(checklist) Spec Editor"
        : "$(warning) Spec Editor";
      statusBar.command = "specEditor.showLog";
      statusBar.tooltip = projectPath
        ? mcpStatusTooltip
        : "MCP running — open a spec-editor project (click for log)";
      statusBar.color = undefined;
      logEvent(
        "OK",
        `StatusBar set: ${statusBar.text.replace("$(", "")} (projectPath=${!!projectPath})`,
      );
      restartCount = 0;
      mcpInitialized = true;
      break;
    } catch {
      await new Promise<void>((r) => setTimeout(r, UI_DELAY.MCP_READY_POLL));
    }
  }

  if (!mcpInitialized) {
    treeView.message = "Failed to start MCP server — see Spec Editor output";
    statusBar.text = "$(error) Spec Editor";
    statusBar.tooltip = `MCP not available on port ${actualPort}`;
    statusBar.color = new vscode.ThemeColor("statusBarItem.errorForeground");
    logEvent(
      "ERROR",
      `MCP initialization failed after ${UI_DELAY.MCP_READY_CYCLES} attempts on port ${actualPort}`,
    );
    return;
  }

  const lastProject: string | undefined =
    extensionContext.workspaceState.get("lastProject");
  // Fallback: use VSCode workspace root if it contains methodology.yaml
  const restorePath: string | undefined =
    lastProject ??
    (workspaceRoot &&
    require("fs").existsSync(path.join(workspaceRoot, "methodology.yaml"))
      ? workspaceRoot
      : undefined);
  logEvent(
    "INFO",
    `project restore: ${restorePath || "null"} (lastProject=${lastProject || "null"})`,
  );
  if (restorePath) {
    try {
      treeView.message = "Loading…";
      await callMcp("switch_project", { path: restorePath });
      await treeProvider.loadElements();
      notifyWebView("projectLoaded");
      vscode.commands.executeCommand(
        "setContext",
        "specEditor.projectLoaded",
        true,
      );
      mcpConnected = true;
      mcpStatusTooltip = `MCP server on port ${activeMcpPort || mcpPort}`;
      statusBar.text = "$(checklist) Spec Editor";
      statusBar.tooltip = mcpStatusTooltip;
      statusBar.color = undefined;
      statusBar.command = "specEditor.showLog";
      logEvent("OK", "StatusBar set: $(checklist) (project restore)");
    } catch {
      treeView.message = undefined;
    }
  }

  // After project restore, check if spec-editor run is still active
  // (e.g. after VSCode window reload while run was in progress)
  try {
    const runStatus: any = await callMcp("is_run_active", {});
    // Parse MCP response: content[0].text is a JSON string
    let runData: any;
    if (runStatus && runStatus.content && runStatus.content[0]) {
      runData = JSON.parse(runStatus.content[0].text);
    } else if (runStatus && typeof runStatus === "object") {
      runData = runStatus;
    }
    if (runData?.active) {
      runActive = true;
      vscode.commands.executeCommand(
        "setContext",
        "specEditor.runActive",
        true,
      );
      statusBar.text = "$(sync~spin) Spec Editor — Running";
      statusBar.command = "specEditor.stopRun";
      statusBar.tooltip = `spec-editor run in progress (PID ${runData.pid})`;
      statusBar.color = new vscode.ThemeColor(
        "statusBarItem.warningForeground",
      );
      outputChannel.info(`Detected running spec-editor (PID ${runData.pid})`);
      logEvent("OK", `Restored runActive=true from PID ${runData.pid}`);

      // Poll for completion since we have no terminal to watch
      const pollInterval: NodeJS.Timeout = setInterval(async () => {
        try {
          const s: any = await callMcp("is_run_active", {});
          let d: any;
          if (s?.content?.[0]) {
            d = JSON.parse(s.content[0].text);
          } else if (s && typeof s === "object") {
            d = s;
          }
          if (!d?.active) {
            clearInterval(pollInterval);
            runActive = false;
            vscode.commands.executeCommand(
              "setContext",
              "specEditor.runActive",
              false,
            );
            statusBar.text = "$(checklist) Spec Editor";
            statusBar.command = "specEditor._quickOpen";
            statusBar.tooltip = "Spec Editor ready";
            statusBar.color = undefined;
            outputChannel.info(
              "spec-editor run completed (detected via poll).",
            );
            logEvent("INFO", "specEditor.run: COMPLETED (poll)");
          }
        } catch {
          // ignore poll errors
        }
      }, 5000);
    }
  } catch {
    // is_run_active not available (old MCP version) — ignore
  }

  outputChannel.info(`MCP server ready on http://127.0.0.1:${mcpPort}/mcp`);
}

async function handleOpenProject(): Promise<void> {
  logEvent("INFO", "handleOpenProject: CALLED");
  let folders: vscode.Uri[] | undefined;
  try {
    folders = await Promise.race<vscode.Uri[] | undefined>([
      vscode.window.showOpenDialog({
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: "Open spec-editor project",
        title: "Select a spec-editor project (contains methodology.yaml)",
      }),
      new Promise<never>((_, reject) =>
        setTimeout(
          () => reject(new Error("Dialog timeout")),
          UI_DELAY.DIALOG_TIMEOUT,
        ),
      ),
    ]);
  } catch (e: any) {
    logEvent("WARN", `handleOpenProject: dialog ${String(e)}`);
    return;
  }

  if (!folders || folders.length === 0) {
    logEvent("INFO", "handleOpenProject: cancelled (no folder selected)");
    return;
  }

  const projectPath: string = folders[0].fsPath;
  logEvent("INFO", `handleOpenProject: selected ${projectPath}`);

  if (!require("fs").existsSync(path.join(projectPath, "methodology.yaml"))) {
    logEvent(
      "WARN",
      `handleOpenProject: no methodology.yaml in ${projectPath}`,
    );
    vscode.window.showErrorMessage(
      "Not a spec-editor project: methodology.yaml not found",
    );
    return;
  }

  treeView.message = "Loading…";
  await callMcp("switch_project", { path: projectPath });
  treeProvider.loadElements();
  extensionContext.workspaceState.update("lastProject", projectPath);
  vscode.window.showInformationMessage(
    `Opened spec-editor project: ${projectPath}`,
  );
}

async function handleNewProject(): Promise<void> {
  const name: string | undefined = await vscode.window.showInputBox({
    prompt: "Project name",
    placeHolder: "my-spec-project",
  });
  if (!name) {
    return;
  }

  const methodologies: string[] = ["waterfall", "agile", "api_first"];
  const method: string | undefined = await vscode.window.showQuickPick(
    methodologies,
    {
      placeHolder: "Select methodology",
    },
  );
  if (!method) {
    return;
  }

  const terminal: vscode.Terminal =
    vscode.window.createTerminal("Spec Editor Init");
  const py: string = detectedPythonPath || pythonPath;
  const wsRoot: string =
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || ".";
  const projDir: string = path.join(wsRoot, name);
  terminal.sendText(
    `mkdir -p "${projDir}" && cd "${projDir}" && "${py}" -m src.main init ${name} --methodology ${method}`,
  );
  terminal.show();
  outputChannel.info(`New project: ${projDir} with ${method}`);
}

function getMethodologyTemplate(method: string): string {
  const templates: Record<string, string> = {
    waterfall: `name: waterfall
version: "1.0"
description: "Classical waterfall requirements development methodology."
aspects:
  - name: sources
    title: Source Requirements
    element_types:
      - name: source
        title: Source Requirement
  - name: modules
    title: Software System Modules
    element_types:
      - name: module
        title: Software Module
  - name: user_scenarios
    title: User Scenarios
    element_types:
      - name: scenario
        title: User Scenario
  - name: user_interface
    title: User Interface
    element_types:
      - name: interface
        title: UI Element
  - name: data_entities
    title: Data Entities
    element_types:
      - name: entity
        title: Data Entity
  - name: nfr
    title: Non-Functional Requirements
    element_types:
      - name: nfr
        title: NFR
  - name: implementation
    title: Implementation Details
    element_types:
      - name: implementation
        title: Implementation
  - name: metrics
    title: Project Metrics
    element_types:
      - name: metric
        title: Metric
`,
    agile: `name: agile
version: "1.0"
description: "Agile requirements development methodology."
aspects:
  - name: epics
    title: Epics
    element_types:
      - name: epic
        title: Epic
  - name: user_stories
    title: User Stories
    element_types:
      - name: story
        title: User Story
  - name: tasks
    title: Tasks
    element_types:
      - name: task
        title: Task
  - name: acceptance
    title: Acceptance Criteria
    element_types:
      - name: criterion
        title: Criterion
`,
    api_first: `name: api-first
version: "1.0"
description: "API-first requirements development methodology."
aspects:
  - name: endpoints
    title: API Endpoints
    element_types:
      - name: endpoint
        title: Endpoint
  - name: schemas
    title: Data Schemas
    element_types:
      - name: schema
        title: Schema
  - name: auth
    title: Authentication
    element_types:
      - name: auth
        title: Auth Rule
  - name: errors
    title: Error Handling
    element_types:
      - name: error
        title: Error Response
`,
  };
  return templates[method] || templates["waterfall"];
}

async function handleViewDiagram(elementId?: string): Promise<void> {
  logEvent("INFO", `viewDiagram: CALLED elementId=${elementId || "none"}`);
  await mcpReady;

  if (activePanel) {
    activePanel.reveal(vscode.ViewColumn.Beside);
    if (elementId) {
      logEvent(
        "INFO",
        `viewDiagram: sending selectElement=${elementId} (panel=${!!activePanel})`,
      );
      activePanel.webview.postMessage({
        type: "specEditor",
        event: "selectElement",
        elementId,
        filterAspect: filterAspect || null,
        filterRelation: filterRelation || null,
      });
    }
    return;
  }

  const panel: vscode.WebviewPanel = vscode.window.createWebviewPanel(
    "specEditorDiagram",
    "Spec Editor",
    vscode.ViewColumn.Beside,
    { enableScripts: true, retainContextWhenHidden: true },
  );
  activePanel = panel;

  panel.onDidDispose(() => {
    activePanel = undefined;
  });

  vscode.commands.executeCommand("specEditor.treeView.focus");
  vscode.commands.executeCommand(
    "workbench.view.extension.spec-editor-sidebar",
  );

  panel.webview.onDidReceiveMessage(async (message: any) => {
    if (message.type === "log") {
      outputChannel.info(`[WebView] ${message.text}`);
      logEvent("INFO", `[WebView] ${message.text}`);
      return;
    }
    if (message.type === "downloadSvg") {
      logEvent("INFO", "[WebView] downloadSvg requested");
      const svgData: string = message.svg || "";
      if (!svgData) return;
      const uri = await vscode.window.showSaveDialog({
        defaultUri: vscode.Uri.file("diagram.svg"),
        filters: { SVG: ["svg"] },
      });
      if (uri) {
        require("fs").writeFileSync(uri.fsPath, svgData);
        vscode.window.showInformationMessage("SVG saved: " + uri.fsPath);
      }
      return;
    }
    if (message.type === "diagramEdgeClick") {
      logEvent("INFO", `[WebView] diagramEdgeClick: ${message.targetId}`);
      handleViewDiagram(message.targetId);
      return;
    }
    if (message.type === "diagramNodeDblClick") {
      logEvent("INFO", `[WebView] diagramNodeDblClick: ${message.nodeId}`);
      try {
        await vscode.commands.executeCommand(
          "specEditor.editElement",
          message.nodeId,
        );
      } catch (e: any) {
        logEvent(
          "WARN",
          `diagramNodeDblClick editElement failed: ${e?.message || e}`,
        );
      }
      return;
    }
    if (message.type === "diagramNodeClick") {
      logEvent("INFO", `[WebView] diagramNodeClick: ${message.nodeId}`);
      handleViewDiagram(message.nodeId);
      return;
    }
    if (message.type === "diagramReady") {
      logEvent(
        "OK",
        `[WebView] diagramReady: SVG count=${message.svgCount || 0}`,
      );
      return;
    }
    if (message.type === "mcp") {
      logEvent(
        "INFO",
        `[WebView MCP] ${message.body.method} bridge_state=${message.body._bridgeState || "unknown"}`,
      );
      try {
        // If this is a tools/call, route through callMcp which has
        // Docker fallback + auto-inject project_path logic.
        // Raw messages like initialize/tools/list go via callMcpRaw.
        let result: any;
        if (message.body.method === "tools/call") {
          const params = message.body.params || {};
          const toolName = params.name || "";
          const toolArgs: Record<string, any> = params.arguments || {};
          result = { result: { content: [{ type: "text", text: await callMcp(toolName, toolArgs) }] } };
        } else {
          result = await callMcpRaw(
            message.body.method,
            message.body.params,
          );
        }
        panel.webview.postMessage({ id: message.body.id, result });
      } catch (err: any) {
        logEvent(
          "WARN",
          `[WebView MCP] ${message.body.method} FAILED: ${err.message}`,
        );
        panel.webview.postMessage({
          id: message.body.id,
          error: err.message,
        });
      }
    }
  });

  const srcDir: vscode.Uri = vscode.Uri.joinPath(
    extensionContext.extensionUri,
    "dist",
    "out",
  );
  const frontendPath: vscode.Uri = vscode.Uri.joinPath(srcDir, "index.html");
  let html: string = require("fs").readFileSync(frontendPath.fsPath, "utf-8");
  const webviewBase: string = panel.webview.asWebviewUri(srcDir).toString();
  html = html.replace(/"\/_next\//g, `"${webviewBase}/_next/`);
  html = html.replace(/"\.\/_next\//g, `"${webviewBase}/_next/`);

  const csp: string = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src ${panel.webview.cspSource} 'unsafe-inline' https://cdn.jsdelivr.net; style-src ${panel.webview.cspSource} 'unsafe-inline'; img-src ${panel.webview.cspSource} data:; connect-src ${panel.webview.cspSource};">`;
  html = html.replace("</head>", csp + "\n</head>");

  const zoomCfg: vscode.WorkspaceConfiguration =
    vscode.workspace.getConfiguration("specEditor");
  const zoomSens: number = zoomCfg.get<number>("zoomSensitivity", 1);
  const diagramEngine: string = zoomCfg.get<string>(
    "diagramEngine",
    "template",
  );
  const bridgeUri = panel.webview.asWebviewUri(
    vscode.Uri.joinPath(extensionContext.extensionUri, "dist", "bridge.js"),
  );
  const projectPath: string =
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";
  const bridgeTag: string = `<script>var WINDOW_ZOOM_SENSITIVITY=${zoomSens};var DIAGRAM_ENGINE=${JSON.stringify(diagramEngine)};var INITIAL_ELEMENT=${JSON.stringify(elementId || null)};var FILTER_ASPECT=${JSON.stringify(filterAspect || null)};var FILTER_RELATION=${JSON.stringify(filterRelation || null)};var __SPEC_EDITOR_PROJECT_PATH__=${JSON.stringify(projectPath)};</script><script src="${bridgeUri}"></script>`;
  html = html.replace("</head>", bridgeTag + "</head>");

  require("fs").writeFileSync("/tmp/spec-editor-webview.html", html);
  panel.webview.html = html;
}

async function handleValidate(): Promise<void> {
  await mcpReady;
  try {
    const result: string = await callMcp("run_validate", {});
    const data: any = JSON.parse(result);
    const status: string = data.passed
      ? "✅ All checks passed"
      : `❌ ${data.errors?.length || 0} errors, ${data.warnings?.length || 0} warnings`;
    vscode.window.showInformationMessage(`Validation: ${status}`);
  } catch {
    const msg: string =
      "MCP server not available. Start a spec-editor project first.";
    logEvent("WARN", `[validate] ${msg}`);
    vscode.window.showWarningMessage(msg);
  }
}

/**
 * Auto-configure the spec-editor MCP server in .vscode/mcp.json
 * so that GitHub Copilot can access specification tools.
 * Only runs if the workspace is a spec-editor project (has methodology.yaml).
 */
async function _ensureMcpJson(): Promise<void> {
  try {
    const wsRoot: string | undefined =
      vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!wsRoot) return;

    // Only configure MCP for spec-editor projects
    if (!require("fs").existsSync(path.join(wsRoot, "methodology.yaml"))) {
      return;
    }

    const vscodeDir = path.join(wsRoot, ".vscode");
    const mcpJsonPath = path.join(vscodeDir, "mcp.json");

    // Don't overwrite if user already configured MCP
    if (require("fs").existsSync(mcpJsonPath)) {
      logEvent("INFO", "_ensureMcpJson: .vscode/mcp.json already exists, skipping");
      return;
    }

    // Find spec-editor binary: prefer workspace .venv, fall back to PATH
    const venvBin = path.join(wsRoot, ".venv", "bin", "spec-editor");
    const command = require("fs").existsSync(venvBin)
      ? "${workspaceFolder}/.venv/bin/spec-editor"
      : "spec-editor";

    const mcpConfig = {
      servers: {
        "spec-editor": {
          type: "stdio",
          command,
          args: ["mcp", "-p", "${workspaceFolder}"],
        },
      },
    };

    // Create .vscode directory if needed
    if (!require("fs").existsSync(vscodeDir)) {
      require("fs").mkdirSync(vscodeDir, { recursive: true });
    }

    require("fs").writeFileSync(
      mcpJsonPath,
      JSON.stringify(mcpConfig, null, 2) + "\n",
      "utf-8",
    );

    logEvent("OK", "_ensureMcpJson: created .vscode/mcp.json");
    outputChannel.info(
      "MCP server configured for Copilot. Reload window to activate.",
    );
  } catch (err: any) {
    logEvent("WARN", `_ensureMcpJson: ${err.message}`);
  }
}

async function findProject(): Promise<string | undefined> {
  const folders: readonly vscode.WorkspaceFolder[] | undefined =
    vscode.workspace.workspaceFolders;
  if (folders) {
    for (const folder of folders) {
      const pattern: vscode.RelativePattern = new vscode.RelativePattern(
        folder,
        "methodology.yaml",
      );
      const files: vscode.Uri[] = await vscode.workspace.findFiles(
        pattern,
        null,
        1,
      );
      if (files.length > 0) {
        return path.dirname(files[0].fsPath);
      }
    }
  }
  return undefined;
}

async function callMcpRaw(method: string, params?: any): Promise<any> {
  const http = require("http");
  const body: string = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method,
    params,
  });
  outputChannel.info(
    `[callMcpRaw] ${method} → port ${activeMcpPort || mcpPort}`,
  );
  return new Promise<any>((resolve, reject) => {
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port: activeMcpPort || mcpPort,
        path: "/mcp",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
        },
      },
      (res: any) => {
        let data: string = "";
        res.on("data", (chunk: string) => (data += chunk));
        res.on("end", () => {
          try {
            resolve(JSON.parse(data));
          } catch {
            reject(new Error(`Invalid JSON: ${data.slice(0, 100)}`));
          }
        });
      },
    );
    req.on("error", (err: Error) => {
      logEvent("WARN", `[callMcpRaw] ${method} FAILED: ${err.message}`);
      reject(err);
    });
    req.write(body);
    req.end();
  });
}

async function callMcp(
  toolName: string,
  args: Record<string, any>,
): Promise<string> {
  // ── Multi-project: auto-inject project_path from workspace root ──
  // Stateless tools (list_projects, analyze_image, list_diagram_types)
  // don't need project_path. All others get the workspace root injected.
  const STATELESS = new Set(["list_projects", "get_project_info", "analyze_image", "list_diagram_types"]);
  if (!STATELESS.has(toolName) && !args.project_path) {
    const wsRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (wsRoot) {
      args = { project_path: wsRoot, ...args };
    }
  }

  // ── Docker path fallback: if local path fails, try /projects/ mapping ──
  let dockerFallback: string | undefined;
  if (args.project_path && args.project_path.startsWith("/")) {
    const folderName = path.basename(args.project_path);
    if (folderName) {
      dockerFallback = "/projects/" + folderName;
    }
  }

  for (let attempt: number = 0; attempt < 5; attempt++) {
    try {
      const data: any = await callMcpRaw("tools/call", {
        name: toolName,
        arguments: args,
      });
      outputChannel.info(
        `[callMcp] ${toolName} response: ${JSON.stringify(data).slice(0, 200)}`,
      );
      if (data.result?.isError) {
        const errText = data.result.content[0]?.text || "MCP error";
        // Docker fallback: if local path failed, retry with /projects/ mapping
        if (dockerFallback && args.project_path && errText.includes("methodology.yaml")) {
          logEvent("INFO", `[callMcp] ${toolName}: Docker fallback ${dockerFallback}`);
          args = { ...args, project_path: dockerFallback };
          dockerFallback = undefined; // only try once
          continue; // retry with Docker path
        }
        throw new Error(errText);
      }
      return data.result?.content[0]?.text || "";
    } catch (err: any) {
      // Docker fallback for network errors (socket hang up, ECONNREFUSED, etc.)
      // Previously only MCP isError triggered the fallback; now network-level
      // failures also try the /projects/ mapping before giving up.
      if (dockerFallback && args.project_path) {
        logEvent(
          "INFO",
          `[callMcp] ${toolName}: network error, Docker fallback ${dockerFallback}`,
        );
        args = { ...args, project_path: dockerFallback };
        dockerFallback = undefined;
        continue; // retry with Docker path
      }
      logEvent(
        "WARN",
        `[callMcp] ${toolName} retry ${attempt + 1}/5: ${err.message}`,
      );
      if (attempt === 4) throw err;
      await new Promise<void>((r) => setTimeout(r, UI_DELAY.MCP_RETRY));
    }
  }
  return "";
}

async function execCommand(
  command: string,
  args: string[],
  timeoutMs: number,
  cwd?: string,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
    const proc: ChildProcess = spawn(command, args, {
      timeout: timeoutMs,
      cwd,
    });
    let stdout: string = "";
    let stderr: string = "";
    proc.stdout?.on("data", (data: Buffer) => {
      stdout += data.toString();
    });
    proc.stderr?.on("data", (data: Buffer) => {
      stderr += data.toString();
    });
    proc.on("close", (code: number | null) => {
      if (code === 0) {
        resolve({ stdout, stderr });
      } else {
        reject(new Error(`Process exited with code ${code}: ${stderr}`));
      }
    });
    proc.on("error", (err: Error) => reject(err));
  });
}

class SpecDragDropController implements vscode.TreeDragAndDropController<SpecTreeItem> {
  dropMimeTypes: readonly string[] = [
    "text/uri-list",
    "application/vnd.code.tree.specEditor.treeView",
  ];

  dragMimeTypes: readonly string[] = [];

  async handleDrag(
    source: readonly SpecTreeItem[],
    dataTransfer: vscode.DataTransfer,
    token: vscode.CancellationToken,
  ): Promise<void> {
    // not supported
  }

  async handleDrop(
    target: SpecTreeItem | undefined,
    dataTransfer: vscode.DataTransfer,
    token: vscode.CancellationToken,
  ): Promise<void> {
    const uris: vscode.Uri[] = [];
    for (const [mime, item] of dataTransfer) {
      if (mime === "text/uri-list") {
        const data: string | undefined = await item.asString();
        if (data) {
          uris.push(
            ...data
              .split("\n")
              .filter((u: string) => u.startsWith("file://"))
              .map((u: string) => vscode.Uri.parse(u.trim())),
          );
        }
      } else if (mime === "application/vnd.code.tree.specEditor.treeView") {
        // internal tree drag – not supported
      } else if ((item as any).asFile) {
        const file = (item as any).asFile();
        if (file) uris.push(vscode.Uri.file((file as any).path));
      }
    }
    if (uris.length === 0) return;

    for (const uri of uris) {
      const fp: string = uri.fsPath;
      if (!require("fs").existsSync(fp)) continue;
      const fileName: string = path.basename(fp);
      const ext: string = path.extname(fp).toLowerCase();
      if (
        ![
          ".md",
          ".txt",
          ".pdf",
          ".html",
          ".htm",
          ".docx",
          ".rst",
          ".adoc",
        ].includes(ext) &&
        ext !== ""
      )
        continue;

      let content: string;
      if (ext === ".md" || ext === ".txt") {
        content = require("fs").readFileSync(fp, "utf-8").slice(0, 5000);
      } else if (ext === ".pdf" || ext === ".html" || ext === ".htm") {
        // Convert via MCP tool (uses SourcePreprocessor from ingestion pipeline)
        try {
          const converted: string = await callMcp("convert_source_file", {
            file_path: fp,
          });
          const parsed: any = JSON.parse(converted);
          content =
            parsed.status === "ok"
              ? parsed.content
              : `Source file: ${fileName}`;
        } catch {
          content = `Source file: ${fileName}`;
        }
      } else {
        content = require("fs").readFileSync(fp, "utf-8").slice(0, 5000);
      }
      const id: string = `SRC-${fileName
        .replace(/[^a-zA-Z0-9]/g, "-")
        .toUpperCase()
        .slice(0, 40)}-${Date.now().toString(36).slice(-4)}`;
      try {
        await callMcp("write_element", {
          aspect: "sources",
          element_type: "source",
          id,
          title: fileName,
          content,
          provenance_source: fileName,
        });
        outputChannel.info(`Imported source: ${fileName} -> ${id}`);
        logEvent("INFO", `dragDrop: imported ${fileName} as ${id}`);
      } catch (e: any) {
        outputChannel.warn(`Import failed for ${fileName}: ${e.message}`);
      }
    }
    treeProvider.loadElements();
  }
}

class SpecTreeProvider implements vscode.TreeDataProvider<SpecTreeItem> {
  _onDidChangeTreeData: vscode.EventEmitter<SpecTreeItem | undefined | void> =
    new vscode.EventEmitter<SpecTreeItem | undefined | void>();
  onDidChangeTreeData: vscode.Event<SpecTreeItem | undefined | void> =
    this._onDidChangeTreeData.event;

  elements: any[] = [];
  _expandedAspects: Set<string> = new Set();
  _aspectItems: Map<string, SpecTreeItem> = new Map();
  _aspectOrder: string[] = [];

  /**
   * Build or reuse a SpecTreeItem for an aspect heading.
   *
   * ⚠️ CRITICAL: VSCode tracks tree items by object identity.
   * Creating a new object for the same aspect on every call causes
   * tree collapse/loss of state after refresh.  Always reuse the
   * cached item when one exists; only create a new object when the
   * cache has no entry for this aspect.
   *
   * DO NOT replace this with a simple "return new SpecTreeItem(...)"
   * — that WILL break the left panel tree after any refresh.
   */
  _makeAspectItem(
    aspect: string,
    collapsible?: vscode.TreeItemCollapsibleState,
  ): SpecTreeItem {
    const els: any[] = this.elements.filter((e: any) => e.aspect === aspect);
    const label = `${this.aspectIcon(aspect)} ${aspect} (${els.length})`;
    const state =
      collapsible ??
      (this._expandedAspects.has(aspect)
        ? vscode.TreeItemCollapsibleState.Expanded
        : vscode.TreeItemCollapsibleState.Collapsed);

    // Reuse cached item to preserve VSCode object identity.
    // Only the label and collapsibleState need updating when elements change.
    const existing = this._aspectItems.get(aspect);
    if (existing) {
      existing.label = label;
      existing.collapsibleState = state;
      // Update context.elements so getChildren() for this aspect sees fresh data
      existing.context = { aspect, elements: els };
      return existing;
    }

    const item = new SpecTreeItem(
      label,
      "",
      state,
      { aspect, elements: els },
      aspect,
    );
    this._aspectItems.set(aspect, item);
    return item;
  }

  /** Return the cached aspect item for incremental fire(), or build one. */
  _getAspectItem(aspect: string): SpecTreeItem {
    return this._aspectItems.get(aspect) ?? this._makeAspectItem(aspect);
  }

  /** Programmatically expand an aspect (used by E2E tests). */
  expandAspect(aspect: string): void {
    this._expandedAspects.add(aspect);
    this._saveExpandedState();
    const els: any[] = this.elements.filter((e: any) => e.aspect === aspect);
    if (els.length === 0) {
      return;
    }
    treeView.reveal(
      this._makeAspectItem(aspect, vscode.TreeItemCollapsibleState.Expanded),
      {
        expand: true,
        select: false,
      },
    );
  }

  getElementCount(): number {
    return this.elements.length;
  }

  /**
   * Persist expanded aspects to workspaceState so they survive
   * full tree refreshes (fire(undefined)). Called before refresh/removeElement.
   */
  _saveExpandedState(): void {
    extensionContext.workspaceState.update("specEditor.expandedAspects", [
      ...this._expandedAspects,
    ]);
  }

  /**
   * Restore expanded aspects from workspaceState after a full tree refresh.
   * Expands all previously-open aspects via treeView.reveal().
   *
   * ⚠️ CRITICAL: Must use _makeAspectItem (not "new SpecTreeItem") so the
   * revealed item is the SAME object that getChildren() returns later.
   * VSCode matches tree items by object identity — if reveal() uses a
   * different object, the tree will not expand and the panel breaks.
   */
  private _restoreExpandedState(): void {
    const saved: string[] | undefined = extensionContext.workspaceState.get(
      "specEditor.expandedAspects",
    );
    if (!saved || saved.length === 0) return;
    logEvent(
      "TRACE",
      `_restoreExpandedState: restoring ${saved.length} aspects: [${saved.join(", ")}]`,
    );

    // After fire(undefined), VSCode asynchronously re-renders the tree.
    // We retry reveal() with increasing delays to ensure the tree is ready.
    const delays = [
      UI_DELAY.TREE_RESTORE_FIRST,
      UI_DELAY.TREE_RESTORE_SECOND,
      UI_DELAY.TREE_RESTORE_FINAL,
    ];
    for (const delayMs of delays) {
      setTimeout(() => {
        for (const aspect of saved) {
          const els: any[] = this.elements.filter(
            (e: any) => e.aspect === aspect,
          );
          if (els.length === 0) continue;
          // Use _makeAspectItem to get the SAME cached object that
          // getChildren() returns — VSCode identity match is required.
          const item = this._makeAspectItem(
            aspect,
            vscode.TreeItemCollapsibleState.Expanded,
          );
          try {
            treeView.reveal(item, { expand: true, select: false });
            logEvent(
              "TRACE",
              `_restoreExpandedState: revealed ${aspect} @${delayMs}ms`,
            );
          } catch {
            logEvent(
              "TRACE",
              `_restoreExpandedState: reveal failed for ${aspect} @${delayMs}ms`,
            );
          }
        }
      }, delayMs);
    }
  }

  /**
   * Full tree refresh: save expanded state, fire to VSCode, restore.
   *
   * ⚠️ fire(undefined) tells VSCode to re-query getChildren() for the
   * entire tree.  _restoreExpandedState() re-expands previously-open
   * aspects.  These MUST use the same cached items (_makeAspectItem)
   * or VSCode will see different object identities and the left panel
   * tree will collapse / go blank.
   */
  refresh(): void {
    this._saveExpandedState();
    this._onDidChangeTreeData.fire(undefined);
    this._restoreExpandedState();
  }

  removeElement(id: string): void {
    // Find the deleted element's aspect before filtering
    const deleted = this.elements.find((e: any) => e.id === id);
    const affectedAspect = deleted?.aspect;

    logEvent(
      "TRACE",
      `removeElement: START id=${id} aspect=${affectedAspect} expanded=[${[...this._expandedAspects].join(",")}] cacheKeys=[${[...this._aspectItems.keys()].join(",")}]`,
    );

    this.elements = this.elements.filter((e: any) => e.id !== id);

    try {
      (globalThis as any).__log?.(
        "OK",
        `removeElement: ${id} from ${affectedAspect}`,
      );
    } catch {}

    // Fire a full refresh, then restore all expanded aspects.
    // We use fire(undefined) because fire(singleItem) is unreliable
    // for incremental updates — VSCode may still re-render the whole tree.
    // _restoreExpandedState() re-expands every previously-open aspect.
    this._saveExpandedState();
    this._onDidChangeTreeData.fire(undefined);
    this._restoreExpandedState();

    logEvent(
      "TRACE",
      `removeElement: DONE expanded=[${[...this._expandedAspects].join(",")}]`,
    );
  }

  async loadElements(): Promise<void> {
    try {
      // Load aspect order from methodology first
      try {
        const methodResult: string = await callMcp("get_methodology", {});
        const methodData: any = JSON.parse(methodResult);
        const aspects: any[] = methodData.aspects || [];
        this._aspectOrder = aspects.map((a: any) => a.name);
        logEvent(
          "TRACE",
          `loadElements: methodology order [${this._aspectOrder.join(", ")}]`,
        );
      } catch {
        // Use default order if methodology unavailable
      }

      const result: string = await callMcp("list_all_elements", {});
      const data: any = JSON.parse(result);
      this.elements = data.elements || [];
      this.refresh();
      treeView.message = this.elements.length
        ? undefined
        : "No elements yet — select methodology to begin";
      vscode.commands.executeCommand(
        "setContext",
        "specEditor.treeEmpty",
        this.elements.length === 0,
      );
    } catch {
      this.elements = [];
      this.refresh();
      treeView.message = "Failed to load elements";
      vscode.commands.executeCommand(
        "setContext",
        "specEditor.treeEmpty",
        true,
      );
    }
  }

  getTreeItem(element: SpecTreeItem): vscode.TreeItem {
    return element;
  }

  /**
   * Return the parent aspect item for an element.
   *
   * ⚠️ CRITICAL: Use _getAspectItem (not _makeAspectItem) so we don't
   * overwrite the cached item's collapsibleState.  Forcing Expanded
   * here would fight the user — if they collapsed the aspect, the
   * tree would auto-expand it on every keyboard navigation.
   */
  getParent(element: SpecTreeItem): vscode.ProviderResult<SpecTreeItem> {
    if (element.contextValue === "element" && element.label) {
      const elId: string = (element.label as string).split(":")[0];
      const el: any = this.elements.find((e: any) => e.id === elId);
      if (el) {
        return this._getAspectItem(el.aspect);
      }
    }
    return null;
  }

  async getChildren(element?: SpecTreeItem): Promise<SpecTreeItem[]> {
    if (!element) {
      if (this.elements.length === 0) {
        if (treeView.message) return [];
        return [
          new SpecTreeItem(
            "No elements loaded — open a project",
            "specEditor.open",
            vscode.TreeItemCollapsibleState.None,
          ),
        ];
      }
      const aspects: Map<string, any[]> = new Map();
      for (const el of this.elements) {
        const a: string = el.aspect || "unknown";
        // Apply relation filter at element level
        if (filterRelation && el.relationships) {
          if (!el.relationships[filterRelation]) continue;
        } else if (filterRelation && !el.relationships) {
          continue;
        }
        if (!aspects.has(a)) aspects.set(a, []);
        aspects.get(a)!.push(el);
      }
      let entries = [...aspects.entries()];
      // Apply aspect filter
      if (filterAspect) {
        entries = entries.filter(([a]) => a === filterAspect);
      }
      return entries
        .sort(([a], [b]) => {
          const ai = this._aspectOrder.indexOf(a);
          const bi = this._aspectOrder.indexOf(b);
          if (ai === -1 && bi === -1) return a.localeCompare(b);
          if (ai === -1) return 1;
          if (bi === -1) return -1;
          return ai - bi;
        })
        .map(([aspect]) => this._makeAspectItem(aspect));
    }

    if (element.context?.elements) {
      const parentAspect: string = element.context.aspect;
      // Show only root elements (no parent), sorted alphabetically
      return element.context.elements
        .filter((el: any) => !el.parent)
        .sort((a: any, b: any) => (a.id || "").localeCompare(b.id || ""))
        .map((el: any) => {
          const hasChildren =
            el.children && (el.children as string[]).length > 0;
          const item = new SpecTreeItem(
            `${el.id}: ${el.title}`,
            "",
            hasChildren
              ? vscode.TreeItemCollapsibleState.Collapsed
              : vscode.TreeItemCollapsibleState.None,
            hasChildren ? { aspect: parentAspect, element: el } : undefined,
            el.id,
            el.id,
            el.status,
          );
          item.contextValue = "element";
          logEvent(
            "TRACE",
            `getChildren: ${hasChildren ? "parent" : "leaf"} ${el.id} children=${el.children?.length || 0}`,
          );
          return item;
        });
    }

    // Children of an element (expand tree): show its child elements
    if (element.context?.element) {
      const parentAspect: string = element.context.aspect;
      const parentEl: any = element.context.element;
      const childIds: string[] = parentEl.children || [];
      return childIds
        .map((cid: string) => {
          const child = this.elements.find(
            (e: any) => e.id === cid && e.aspect === parentAspect,
          );
          if (!child) return null;
          const hasChildren =
            child.children && (child.children as string[]).length > 0;
          const item = new SpecTreeItem(
            `${child.id}: ${child.title}`,
            "",
            hasChildren
              ? vscode.TreeItemCollapsibleState.Collapsed
              : vscode.TreeItemCollapsibleState.None,
            hasChildren ? { aspect: parentAspect, element: child } : undefined,
            child.id,
            child.id,
            child.status,
          );
          item.contextValue = "element";
          return item;
        })
        .filter(Boolean)
        .sort((a: any, b: any) => (a.id || "").localeCompare(b.id || "")) as SpecTreeItem[];
    }

    return [];
  }

  private aspectIcon(aspect: string): string {
    const icons: Record<string, string> = {
      modules: "\u{1F9E9}",
      user_scenarios: "\u{1F464}",
      user_interface: "\u{1F5A5}\uFE0F",
      ui_states: "\u{1F5A5}\uFE0F",
      data_entities: "\u{1F5C4}\uFE0F",
      nfr: "\u26A1",
      non_functional: "\u26A1",
      implementation: "\u{1F527}",
      metrics: "\u{1F4CA}",
      sources: "\u{1F4C4}",
    };
    return icons[aspect] || "\u{1F4E6}";
  }
}

class SpecTreeItem extends vscode.TreeItem {
  context: any;

  constructor(
    label: string,
    commandId: string,
    collapsibleState: vscode.TreeItemCollapsibleState,
    context?: any,
    elementId?: string,
    clickTarget?: string, // what to pass to viewDiagram on click
    elementStatus?: string, // draft, reviewed, confirmed — for icon color
  ) {
    super(label, collapsibleState);
    this.context = context;
    if (commandId) {
      this.command = { command: commandId, title: label };
    }
    if (elementId || clickTarget) {
      this.id = elementId || clickTarget;
      this.tooltip = elementId
        ? `Click to view ${elementId}`
        : `Click to view ${clickTarget}`;
      this.command = {
        command: "specEditor.viewDiagram",
        title: "View Element",
        arguments: [clickTarget || elementId],
      };
    }

    // Color-coded label by element status
    if (elementStatus) {
      this.description = elementStatus;

      // VSCode TreeItem.color is unreliable across versions.
      // Use colored circle symbol before the label instead.
      const symbolMap: Record<string, string> = {
        draft: "🟠 ", // orange circle
        reviewed: "🟢 ", // green circle
        confirmed: "🔵 ", // blue circle
        deprecated: "⚫ ", // gray circle
      };
      const symbol = symbolMap[elementStatus] || "";
      if (typeof this.label === "string" && symbol) {
        // Only add symbol if not already present
        const labelStr = this.label as string;
        if (
          !labelStr.startsWith("🟠") &&
          !labelStr.startsWith("🟢") &&
          !labelStr.startsWith("🔵") &&
          !labelStr.startsWith("⚫")
        ) {
          this.label = symbol + labelStr;
        }
      }
    }
  }
}
