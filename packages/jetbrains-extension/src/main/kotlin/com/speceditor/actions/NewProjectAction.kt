package com.speceditor.actions

import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.speceditor.SpecEditorSettings

/**
 * Creates a new spec-editor project.
 *
 * Prompts for project name and methodology, then runs:
 *   spec-editor init <name> --methodology <method>
 */
class NewProjectAction : AnAction() {

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return

        val name = Messages.showInputDialog(
            project,
            "Enter project name:",
            "New Spec Editor Project",
            Messages.getQuestionIcon()
        ) ?: return

        val methodologies = arrayOf("waterfall", "agile", "api_first")
        val method = Messages.showEditableChooseDialog(
            "Select methodology:",
            "New Project — Methodology",
            Messages.getQuestionIcon(),
            methodologies,
            "waterfall",
            null
        ) ?: return

        val basePath = project.basePath ?: return
        val projDir = java.io.File(basePath, name)
        val settings = SpecEditorSettings.getInstance()

        try {
            projDir.mkdirs()
            val pb = ProcessBuilder(
                settings.state.pythonPath, "-m", "src.main", "init", name,
                "--methodology", method
            ).apply {
                directory(java.io.File(basePath))
                redirectErrorStream(true)
            }
            val process = pb.start()
            val output = process.inputStream.bufferedReader().readText()
            process.waitFor()

            val notification = NotificationGroupManager.getInstance()
                .getNotificationGroup("Spec Editor")
                .createNotification(
                    "Project '$name' created with $method methodology in ${projDir.absolutePath}",
                    NotificationType.INFORMATION
                )
            notification.notify(project)
        } catch (ex: Exception) {
            Messages.showErrorDialog(
                project,
                "Failed to create project: ${ex.message}",
                "Spec Editor"
            )
        }
    }

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabled = e.project != null
    }
}

