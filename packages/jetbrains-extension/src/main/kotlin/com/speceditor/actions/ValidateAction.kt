package com.speceditor.actions

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.Messages
import com.speceditor.SpecEditorProjectService
import com.speceditor.mcp.McpClient

/**
 * Runs specification validation via MCP server.
 */
class ValidateAction : AnAction() {

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val service = SpecEditorProjectService.getInstance(project)

        if (service.projectPath == null) {
            Messages.showInfoMessage(project, "No spec-editor project open.", "Spec Editor")
            return
        }

        try {
            val client = McpClient("http://127.0.0.1:${service.mcpPort}/mcp")
            client.callTool("run_validate", emptyMap())

            Messages.showInfoMessage(project, "Validation started. Check the output.", "Spec Editor")
        } catch (ex: Exception) {
            Messages.showErrorDialog(project, "Validation failed: ${ex.message}", "Spec Editor")
        }
    }

    override fun update(e: AnActionEvent) {
        val project = e.project
        e.presentation.isEnabled = project != null &&
            SpecEditorProjectService.getInstance(project).projectPath != null
    }
}
