package com.speceditor

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.PersistentStateComponent
import com.intellij.openapi.components.State
import com.intellij.openapi.components.Storage
import com.intellij.util.xmlb.XmlSerializerUtil

/**
 * Persistent plugin settings (application-level).
 *
 * Stored in IDE config directory, survives restarts.
 * Configurable via Settings → Tools → Spec Editor.
 *
 * Note: This is a simplified settings holder. The full settings
 * with agent configs are in SpecEditorPlugin.SpecEditorSettings.State.
 * This class is kept for backward compatibility with the configurable UI.
 */
@State(
    name = "SpecEditorSettings",
    storages = [Storage("spec-editor.xml")]
)
class SpecEditorSettings : PersistentStateComponent<SpecEditorSettings> {

    var pythonPath: String = "python3"
    var mcpPort: Int = 8088
    var autoStartMcp: Boolean = true

    override fun getState(): SpecEditorSettings = this

    override fun loadState(state: SpecEditorSettings) {
        XmlSerializerUtil.copyBean(state, this)
    }

    companion object {
        val instance: SpecEditorSettings
            get() = ApplicationManager.getApplication()
                .getService(SpecEditorSettings::class.java)
    }
}

