package com.speceditor.ui

import com.intellij.openapi.Disposable
import com.intellij.openapi.project.Project
import com.intellij.ui.jcef.JBCefBrowser
import com.intellij.ui.jcef.JBCefClient
import java.net.HttpURLConnection
import java.net.URI
import javax.swing.JComponent

/**
 * JCEF (Chromium) browser panel loading the spec-editor frontend.
 *
 * Priority:
 * 1. Dev server (http://localhost:3000) — for development
 * 2. Bundled static files (from plugin resources/frontend/) — for production
 *
 * Communication with the MCP server happens through the frontend's
 * existing McpClient (TypeScript) → http://127.0.0.1:<port>/mcp.
 */
class SpecEditorBrowserPanel(
    private val project: Project,
    private val mcpPort: Int
) : Disposable {

    val browser: JBCefBrowser = JBCefBrowser()
    val component: JComponent = browser.component

    init {
        val frontendUrl = resolveFrontendUrl()
        browser.loadURL(frontendUrl)

        // Inject MCP port + adapter info BEFORE page loads (via LoadHandler is too late)
        val jsCode = """
            window.__SPEC_EDITOR_MCP_PORT__ = $mcpPort;
            window.__SPEC_EDITOR_ADAPTER__ = 'jetbrains';
        """.trimIndent()

        browser.jbCefClient.addLoadHandler { _, _ ->
            browser.cefBrowser.executeJavaScript(jsCode, browser.cefBrowser.url, 0)
        }
    }

    /**
     * Resolve frontend URL:
     * 1. Try localhost:3000 (dev server) — check if reachable
     * 2. Fall back to bundled resource (production build)
     */
    private fun resolveFrontendUrl(): String {
        // Check if dev server is running
        val devUrl = "http://localhost:3000"
        if (isUrlReachable(devUrl)) {
            return devUrl
        }

        // Try bundled frontend
        val bundledPath = javaClass.classLoader.getResource("frontend/index.html")
        if (bundledPath != null) {
            return bundledPath.toString()
        }

        // Fallback: still try dev server (will show connection error if not available)
        return devUrl
    }

    private fun isUrlReachable(url: String): Boolean {
        return try {
            val connection = URI(url).toURL().openConnection() as HttpURLConnection
            connection.connectTimeout = 1000
            connection.readTimeout = 1000
            connection.requestMethod = "HEAD"
            connection.responseCode in 200..399
        } catch (_: Exception) {
            false
        }
    }

    fun reload() {
        browser.loadURL(resolveFrontendUrl())
    }

    override fun dispose() {
        browser.dispose()
    }
}

