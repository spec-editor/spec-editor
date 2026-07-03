package com.speceditor

import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.project.Project

/**
 * Project-level service that holds spec-editor MCP state.
 *
 * One instance per open project. Stores the project path, MCP process
 * handle, and cached methodology information.
 */
@Service(Service.Level.PROJECT)
class SpecEditorProjectService(private val project: Project) {

    var projectPath: String? = null
    var mcpPort: Int = 8088
    var isMcpRunning: Boolean = false
    var methodologyName: String? = null

    companion object {
        fun getInstance(project: Project): SpecEditorProjectService =
            project.service()
    }
}
