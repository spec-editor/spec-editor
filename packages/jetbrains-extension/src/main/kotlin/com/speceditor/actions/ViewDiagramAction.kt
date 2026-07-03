package com.speceditor.actions

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.Messages
import com.speceditor.SpecEditorProjectService
import com.speceditor.mcp.McpClient
import com.google.gson.JsonParser

/**
 * Generates and displays a Mermaid diagram for the current spec project.
 */
class ViewDiagramAction : AnAction() {

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val service = SpecEditorProjectService.getInstance(project)

        if (service.projectPath == null) {
            Messages.showInfoMessage(
                project,
                "No spec-editor project open. Use 'Spec Editor: Open Project' first.",
                "Spec Editor"
            )
            return
        }

        try {
            val client = McpClient("http://127.0.0.1:${service.mcpPort}/mcp")
            val result = client.callTool("generate_diagram", emptyMap())

            val content = result.getAsJsonArray("content")?.get(0)?.asJsonObject
            val text = content?.get("text")?.asString ?: return
            val diagramData = JsonParser.parseString(text).asJsonObject
            val mermaidCode = diagramData.get("diagram")?.asString ?: "No diagram generated"

            Messages.showMultilineInputDialog(
                project,
                "Mermaid Diagram (copy to mermaid.live)",
                "Spec Diagram",
                mermaidCode,
                null,
                null
            )
        } catch (ex: Exception) {
            Messages.showErrorDialog(
                project,
                "Diagram generation failed: ${ex.message}",
                "Spec Editor"
            )
        }
    }

    override fun update(e: AnActionEvent) {
        val project = e.project
        e.presentation.isEnabled = project != null &&
            SpecEditorProjectService.getInstance(project).projectPath != null
    }
}
