package com.speceditor.ui

import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.content.ContentFactory
import com.speceditor.SpecEditorProjectService
import com.speceditor.SpecEditorSettings
import com.speceditor.McpServerManager

/**
 * Tool window factory for Spec Editor panel.
 *
 * Creates a JCEF (Chromium) browser that loads the spec-editor
 * frontend. The frontend communicates with the Python MCP server
 * via JSON-RPC over HTTP.
 *
 * Production: loads from bundled static files (resources/frontend/).
 * Development: loads from http://localhost:3000 if dev server is running.
 */
class SpecEditorToolWindow : ToolWindowFactory {

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val service = SpecEditorProjectService.getInstance(project)

        // Start MCP server if not running and project is detected
        if (!service.isMcpRunning && service.projectPath != null) {
            val settings = SpecEditorSettings.getInstance()
            val manager = McpServerManager(service.projectPath!!)
            if (manager.start(settings.state)) {
                service.mcpPort = manager.port
                service.isMcpRunning = true
            }
        }

        // Create browser panel with frontend
        val panel = SpecEditorBrowserPanel(project, service.mcpPort)
        val content = ContentFactory.getInstance()
            .createContent(panel.component, "Spec Editor", false)

        toolWindow.contentManager.addContent(content)
    }

    override fun isApplicable(project: Project): Boolean = true
}

