/**
 * Spec Editor — JetBrains Extension
 *
 * Starts the spec-editor MCP server on project open and provides
 * a tool window with embedded frontend (JCEF browser).
 *
 * Communication: MCP JSON-RPC over HTTP to Python backend.
 *
 * References:
 *   EXT-INTEGRATION: Extension API Integration Plan
 */

package com.speceditor

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.*
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.jcef.JBCefBrowser
import com.intellij.openapi.util.Disposer
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.File

// =============================================================================
// Constants
// =============================================================================

private val LOG = Logger.getInstance(SpecEditorPlugin::class.java)
private const val MCP_PORT = 8088  // synced with src/mcp/server.py:_DEFAULT_MCP_PORT
private const val TOOL_WINDOW_ID = "Spec Editor"
private const val MAX_RESTARTS = 3

// =============================================================================
// Plugin settings (persistent)
// =============================================================================

@State(
    name = "SpecEditorSettings",
    storages = [Storage("spec-editor.xml")]
)
class SpecEditorSettings : PersistentStateComponent<SpecEditorSettings.State> {
    data class AgentConfig(
        var provider: String = "openai",
        var model: String = "gpt-4o",
        var temperature: Double = 0.7,
        var maxTokens: Int = 4096
    )

    data class State(
        var pythonPath: String = "python3",
        var mcpPort: Int = MCP_PORT,
        var autoStartMcp: Boolean = true,
        var frontendPort: Int = 3000,
        var agent1: AgentConfig = AgentConfig(),
        var agent2: AgentConfig = AgentConfig(),
        var orchestrator: AgentConfig = AgentConfig(),
        var maxRounds: Int = 20,
        var maxTimeMinutes: Int = 480,
        var maxLlmCalls: Int = 30,
        var tokenBudget: Int = 50000,
        var llmRequestTimeout: Int = 90,
        var llmTotalTimeout: Int = 90,
        var restrictSourceDeletion: Boolean = true
    )

    var state = State()

    override fun getState(): State = state
    override fun loadState(s: State) { state = s }

    companion object {
        fun getInstance(): SpecEditorSettings =
            ApplicationManager.getApplication().getService(SpecEditorSettings::class.java)
    }
}

// =============================================================================
// Smart Python detection (port of VSCode detectPython)
// =============================================================================

object PythonDetector {
    data class DetectionResult(
        val pythonPath: String,
        val trace: List<String>
    )

    fun detect(configPythonPath: String, projectPath: String?): DetectionResult {
        val trace = mutableListOf<String>()
        val candidates = mutableListOf<String>()

        if (configPythonPath.isNotBlank()) {
            candidates.add(configPythonPath)
        }

        val home = System.getProperty("user.home")

        if (projectPath != null) {
            candidates.add("$projectPath/.venv/bin/python")
            candidates.add("$projectPath/.venv/bin/python3")
        }

        try {
            val proc = ProcessBuilder("/bin/sh", "-lc", "which python3 2>/dev/null || echo ''")
                .start()
            val out = proc.inputStream.bufferedReader().readText().trim()
            if (out.isNotBlank() && out !in candidates) {
                candidates.add(out)
            }
        } catch (_: Exception) {}

        candidates.add("$home/.local/bin/python3")
        candidates.add("$home/.local/bin/python")

        val devPaths = listOf(
            "$home/Documents/Droid/spec-editor2/.venv/bin/python",
            "$home/Documents/Droid/spec-editor2/.venv/bin/python3",
            "$home/spec-editor2/.venv/bin/python",
            "$home/spec-editor2/.venv/bin/python3"
        )
        for (dp in devPaths) {
            if (File(dp).exists() && dp !in candidates) {
                candidates.add(dp)
            }
        }

        if (configPythonPath.isBlank()) {
            candidates.add("python3")
            candidates.add("python")
        }

        trace.add("detectPython: ${candidates.size} candidates: ${candidates.take(5).joinToString(", ")}...")

        for (candidate in candidates) {
            try {
                val proc = ProcessBuilder(candidate, "-c", "import sys; print(sys.executable)")
                    .start()
                val found = proc.inputStream.bufferedReader().readText().trim()
                if (found.isNotBlank()) {
                    val verProc = ProcessBuilder(found, "--version").start()
                    val exitCode = verProc.waitFor()
                    if (exitCode == 0) {
                        trace.add("DETECT: $found")
                        LOG.info("Python detected: $found")
                        return DetectionResult(found, trace)
                    }
                }
            } catch (_: Exception) {
                // try next
            }
        }

        trace.add("FALLBACK: ${configPythonPath.ifBlank { "python3" }}")
        return DetectionResult(configPythonPath.ifBlank { "python3" }, trace)
    }
}

// =============================================================================
// MCP Server lifecycle
// =============================================================================

class McpServerManager(private val projectPath: String) {
    private var process: Process? = null
    private var restartCount = 0
    private var activePort: Int = MCP_PORT

    val port: Int get() = activePort

    fun start(settings: SpecEditorSettings.State): Boolean {
        val detection = PythonDetector.detect(settings.pythonPath, projectPath)
        val python = detection.pythonPath
        LOG.info("Python detection trace: ${detection.trace.joinToString(" → ")}")

        val importCode = "from src.mcp.server import MCPHandler, run_http_server; print('ok')"
        var moduleFound = false

        val srcInit = File(projectPath, "src/__init__.py")
        val cwd = if (srcInit.exists()) {
            try {
                val proc = ProcessBuilder(python, "-c", importCode)
                    .directory(File(projectPath))
                    .start()
                val out = proc.inputStream.bufferedReader().readText().trim()
                proc.waitFor()
                out == "ok"
            } catch (_: Exception) { false }
        } else {
            try {
                val proc = ProcessBuilder(python, "-c", importCode).start()
                val out = proc.inputStream.bufferedReader().readText().trim()
                proc.waitFor()
                out == "ok"
            } catch (_: Exception) { false }
        }

        if (!moduleFound) {
            LOG.warn("spec-editor module not found for python: $python")
            return false
        }

        activePort = settings.mcpPort
        for (attempt in 0..9) {
            if (isPortFree(activePort)) break
            activePort++
        }

        spawnProcess(python, settings)
        return waitForReady()
    }

    private fun spawnProcess(python: String, settings: SpecEditorSettings.State) {
        val escapedPath = projectPath.replace("\\", "\\\\").replace("\"", "\\\"")
        val serverCode = buildString {
            append("from pathlib import Path; ")
            append("from src.mcp.server import MCPHandler, run_http_server; ")
            append("handler = MCPHandler(project_path=Path(\"$escapedPath\"), writable=True); ")
            append("run_http_server(handler, '127.0.0.1', $activePort)")
        }

        val env = mutableMapOf<String, String>()
        env.putAll(System.getenv())

        try {
            val key = PasswordSafeUtil.getApiKey()
            if (key != null) {
                env["LLM_API_KEY"] = key
                env["DEEPSEEK_API_KEY"] = key
                env["OPENAI_API_KEY"] = key
                env["ANTHROPIC_API_KEY"] = key
            }
        } catch (_: Exception) {}

        fun injectAgent(prefix: String, cfg: SpecEditorSettings.AgentConfig) {
            env["${prefix}__PROVIDER"] = cfg.provider
            env["${prefix}__MODEL"] = cfg.model
            env["${prefix}__TEMPERATURE"] = cfg.temperature.toString()
            env["${prefix}__MAX_TOKENS"] = cfg.maxTokens.toString()
        }
        injectAgent("SPEC_EDITOR__AGENT_1", settings.agent1)
        injectAgent("SPEC_EDITOR__AGENT_2", settings.agent2)
        injectAgent("SPEC_EDITOR__ORCHESTRATOR", settings.orchestrator)
        env["SPEC_EDITOR__RESTRICT_SOURCE_DELETION"] = settings.restrictSourceDeletion.toString()

        // PYTHONPATH
        var repoRoot = projectPath
        var candidateDir: File? = File(python).parentFile
        for (i in 0..5) {
            if (candidateDir != null && File(candidateDir, "src/mcp/server.py").exists()) {
                repoRoot = candidateDir.absolutePath
                break
            }
            candidateDir = candidateDir?.parentFile
        }
        if (File(repoRoot, "src/mcp/server.py").exists()) {
            env["PYTHONPATH"] = repoRoot
        }

        val pb = ProcessBuilder(python, "-c", serverCode)
            .directory(File(projectPath))
            .redirectErrorStream(true)

        pb.environment().putAll(env)

        try {
            process = pb.start()
            LOG.info("MCP server started: PID=${process?.pid()}, port=$activePort, python=$python")

            Thread {
                BufferedReader(InputStreamReader(process!!.inputStream)).use { reader ->
                    reader.lines().forEach { line ->
                        LOG.debug("[MCP] $line")
                    }
                }
            }.apply {
                isDaemon = true
                name = "spec-editor-mcp-stdout"
                start()
            }

            Thread {
                val exitCode = process?.waitFor()
                LOG.warn("[MCP] EXITED code=$exitCode restarts=$restartCount/$MAX_RESTARTS")
                process = null
                if (exitCode != 0 && exitCode != null && restartCount < MAX_RESTARTS) {
                    restartCount++
                    LOG.info("MCP restart $restartCount/$MAX_RESTARTS...")
                    Thread.sleep(2000)
                    spawnProcess(python, settings)
                    waitForReady()
                }
            }.apply {
                isDaemon = true
                name = "spec-editor-mcp-monitor"
                start()
            }

        } catch (e: Exception) {
            LOG.error("Failed to start MCP server: ${e.message}", e)
        }
    }

    private fun waitForReady(): Boolean {
        for (i in 0..14) {
            try {
                val client = com.speceditor.mcp.McpClient("http://127.0.0.1:$activePort/mcp")
                client.initialize()
                LOG.info("MCP server ready on port $activePort")
                restartCount = 0
                return true
            } catch (_: Exception) {
                Thread.sleep(300)
            }
        }
        LOG.error("MCP server failed to become ready on port $activePort")
        return false
    }

    fun stop() {
        process?.let {
            if (it.isAlive) {
                it.destroy()
                LOG.info("MCP server stopped")
            }
        }
    }

    companion object {
        private fun isPortFree(port: Int): Boolean {
            return try {
                java.net.ServerSocket(port).use { true }
            } catch (_: Exception) {
                false
            }
        }
    }
}

// =============================================================================
// Password safe utility
// =============================================================================

object PasswordSafeUtil {
    private const val KEY = "specEditor.apiKey"

    fun getApiKey(): String? {
        return try {
            val attrs = com.intellij.credentialStore.CredentialAttributes("SpecEditor", KEY)
            com.intellij.ide.passwordSafe.PasswordSafe.instance.getPassword(attrs)
        } catch (_: Exception) { null }
    }

    fun setApiKey(key: String) {
        try {
            val attrs = com.intellij.credentialStore.CredentialAttributes("SpecEditor", KEY)
            com.intellij.ide.passwordSafe.PasswordSafe.instance.setPassword(attrs, key)
        } catch (_: Exception) {}
    }
}

// =============================================================================
// Project startup — start MCP server
// =============================================================================

class SpecEditorStartup : ProjectActivity {
    override suspend fun execute(project: Project) {
        val settings = SpecEditorSettings.getInstance()
        if (!settings.state.autoStartMcp) {
            LOG.info("Spec Editor auto-start disabled")
            return
        }

        val projectPath = project.basePath ?: return
        val methodologyFile = java.io.File(projectPath, "methodology.yaml")
        if (!methodologyFile.exists()) {
            LOG.info("No methodology.yaml found in $projectPath — not a spec-editor project")
            return
        }

        LOG.info("Spec Editor project detected: $projectPath")

        val service = SpecEditorProjectService.getInstance(project)
        val manager = McpServerManager(projectPath)
        if (manager.start(settings.state)) {
            service.mcpPort = manager.port
            service.isMcpRunning = true
            service.projectPath = projectPath
        }
    }
}

// =============================================================================
// Tool window — embedded frontend
// =============================================================================

class SpecEditorToolWindowFactory : ToolWindowFactory {
    override fun createToolWindowContent(project: Project, toolWindow: com.intellij.openapi.wm.ToolWindow) {
        val service = SpecEditorProjectService.getInstance(project)

        if (!service.isMcpRunning && service.projectPath != null) {
            val settings = SpecEditorSettings.getInstance()
            val manager = McpServerManager(service.projectPath!!)
            if (manager.start(settings.state)) {
                service.mcpPort = manager.port
                service.isMcpRunning = true
            }
        }

        val panel = com.speceditor.ui.SpecEditorBrowserPanel(project, service.mcpPort)
        val content = ContentFactory.getInstance()
            .createContent(panel.component, "", false)
        toolWindow.contentManager.addContent(content)

        Disposer.register(project) { panel.dispose() }
    }

    override fun isApplicable(project: Project): Boolean = true
}

