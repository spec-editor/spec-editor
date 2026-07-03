package com.speceditor.actions

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.Messages
import com.speceditor.PasswordSafeUtil

/**
 * Sets the LLM API key, stored in the OS keychain via IntelliJ PasswordSafe.
 */
class SetApiKeyAction : AnAction() {

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return

        val key = Messages.showPasswordDialog(
            project,
            "Enter your LLM API key (stored securely in OS keychain):",
            "Spec Editor — Set API Key",
            Messages.getQuestionIcon()
        ) ?: return

        PasswordSafeUtil.setApiKey(key)
        Messages.showInfoMessage(project, "API key saved securely.", "Spec Editor")
    }

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabled = true
    }
}
