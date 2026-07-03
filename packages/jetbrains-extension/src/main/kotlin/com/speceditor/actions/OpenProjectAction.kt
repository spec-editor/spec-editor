package com.speceditor.actions

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.fileChooser.FileChooserDescriptorFactory
import com.intellij.openapi.fileChooser.FileChooserFactory
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.speceditor.SpecEditorProjectService
import com.speceditor.mcp.McpClient

/**
 * Opens a spec-editor project by selecting a directory containing
 * methodology.yaml.
 */
class OpenProjectAction : AnAction() {

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return

        val descriptor = FileChooserDescriptorFactory.createSingleFolderDescriptor()
            .withTitle("Select Spec Editor Project")
            .withDescription("Choose a directory containing methodology.yaml")

        val chooser = FileChooserFactory.getInstance()
            .createFileChooser(descriptor, project, null)

        val files = chooser.choose(project)
        if (files.isEmpty()) return

        val chosen = files[0]
        val methodologyFile = chosen.findChild("methodology.yaml")
        if (methodologyFile == null) {
            Messages.showErrorDialog(
                project,
                "No methodology.yaml found in '${chosen.name}'. Run 'spec-editor init' first.",
                "Not a Spec Editor Project"
            )
            return
        }

        val service = SpecEditorProjectService.getInstance(project)
        service.projectPath = chosen.path

        // Switch MCP project
        try {
            val client = McpClient("http://127.0.0.1:${service.mcpPort}/mcp")
            client.callTool("switch_project", mapOf("path" to chosen.path))
        } catch (_: Exception) {
            // MCP server might not be running yet
        }

        Messages.showInfoMessage(
            project,
            "Opened project: ${chosen.path}",
            "Spec Editor"
        )
    }

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabled = e.project != null
    }
}
