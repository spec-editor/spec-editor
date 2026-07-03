package com.speceditor

import com.intellij.openapi.options.Configurable
import com.intellij.ui.components.JBCheckBox
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBTextField
import com.intellij.ui.components.JBPasswordField
import com.intellij.util.ui.FormBuilder
import javax.swing.JComponent
import javax.swing.JPanel
import javax.swing.JTabbedPane
import javax.swing.JComboBox
import javax.swing.JSpinner
import javax.swing.SpinnerNumberModel

/**
 * Settings UI for Spec Editor plugin.
 *
 * Configurable through:
 * Settings → Tools → Spec Editor
 */
class SpecEditorSettingsConfigurable : Configurable {

    // Server
    private var pythonPathField = JBTextField()
    private var mcpPortField = JBTextField()
    private var autoStartCheckbox = JBCheckBox("Auto-start MCP server on project open")
    private var apiKeyField = JBPasswordField()

    // Agent 1
    private var a1Provider = JComboBox(arrayOf("openai", "anthropic", "deepseek", "google", "groq", "ollama"))
    private var a1Model = JBTextField("gpt-4o")
    private var a1Temp = JSpinner(SpinnerNumberModel(0.7, 0.0, 2.0, 0.1))
    private var a1MaxTokens = JSpinner(SpinnerNumberModel(4096, 256, 128000, 256))

    // Agent 2
    private var a2Provider = JComboBox(arrayOf("openai", "anthropic", "deepseek", "google", "groq", "ollama"))
    private var a2Model = JBTextField("gpt-4o")
    private var a2Temp = JSpinner(SpinnerNumberModel(0.7, 0.0, 2.0, 0.1))
    private var a2MaxTokens = JSpinner(SpinnerNumberModel(4096, 256, 128000, 256))

    // Orchestrator
    private var orchProvider = JComboBox(arrayOf("openai", "anthropic", "deepseek", "google", "groq", "ollama"))
    private var orchModel = JBTextField("gpt-4o")
    private var orchTemp = JSpinner(SpinnerNumberModel(0.7, 0.0, 2.0, 0.1))
    private var orchMaxTokens = JSpinner(SpinnerNumberModel(4096, 256, 128000, 256))

    // Limits
    private var maxRounds = JSpinner(SpinnerNumberModel(20, 1, 100, 1))
    private var maxTimeMinutes = JSpinner(SpinnerNumberModel(480, 1, 1440, 5))
    private var maxLlmCalls = JSpinner(SpinnerNumberModel(30, 1, 200, 1))
    private var tokenBudget = JSpinner(SpinnerNumberModel(50000, 1000, 500000, 1000))
    private var llmRequestTimeout = JSpinner(SpinnerNumberModel(90, 10, 600, 5))
    private var restrictSourceCheckbox = JBCheckBox("Protect source elements (SRC-*) from deletion")

    private val settings = SpecEditorSettings.getInstance()

    override fun getDisplayName(): String = "Spec Editor"

    override fun createComponent(): JComponent {
        loadValues()

        val serverPanel = FormBuilder.createFormBuilder()
            .addLabeledComponent(JBLabel("Python path:"), pythonPathField)
            .addLabeledComponent(JBLabel("MCP port:"), mcpPortField)
            .addComponent(autoStartCheckbox)
            .addLabeledComponent(JBLabel("API key (stored in OS keychain):"), apiKeyField)
            .addComponentFillVertically(JPanel(), 0)
            .panel

        fun agentPanel(
            provider: JComboBox<String>,
            model: JBTextField,
            temp: JSpinner,
            maxTokens: JSpinner
        ) = FormBuilder.createFormBuilder()
            .addLabeledComponent(JBLabel("Provider:"), provider)
            .addLabeledComponent(JBLabel("Model:"), model)
            .addLabeledComponent(JBLabel("Temperature:"), temp)
            .addLabeledComponent(JBLabel("Max tokens:"), maxTokens)
            .addComponentFillVertically(JPanel(), 0)
            .panel

        val agent1Panel = agentPanel(a1Provider, a1Model, a1Temp, a1MaxTokens)
        val agent2Panel = agentPanel(a2Provider, a2Model, a2Temp, a2MaxTokens)
        val orchPanel = agentPanel(orchProvider, orchModel, orchTemp, orchMaxTokens)

        val limitsPanel = FormBuilder.createFormBuilder()
            .addLabeledComponent(JBLabel("Max rounds:"), maxRounds)
            .addLabeledComponent(JBLabel("Max time (minutes):"), maxTimeMinutes)
            .addLabeledComponent(JBLabel("Max LLM calls per run:"), maxLlmCalls)
            .addLabeledComponent(JBLabel("Token budget:"), tokenBudget)
            .addLabeledComponent(JBLabel("LLM request timeout (seconds):"), llmRequestTimeout)
            .addComponent(restrictSourceCheckbox)
            .addComponentFillVertically(JPanel(), 0)
            .panel

        val tabs = JTabbedPane()
        tabs.addTab("Server", serverPanel)
        tabs.addTab("Agent 1", agent1Panel)
        tabs.addTab("Agent 2", agent2Panel)
        tabs.addTab("Orchestrator", orchPanel)
        tabs.addTab("Limits", limitsPanel)

        return tabs
    }

    private fun loadValues() {
        val s = settings.state
        pythonPathField.text = s.pythonPath
        mcpPortField.text = s.mcpPort.toString()
        autoStartCheckbox.isSelected = s.autoStartMcp

        val key = PasswordSafeUtil.getApiKey()
        if (key != null) apiKeyField.text = key

        fun loadAgent(
            provider: JComboBox<String>, model: JBTextField,
            temp: JSpinner, maxTokens: JSpinner,
            cfg: SpecEditorSettings.AgentConfig
        ) {
            provider.selectedItem = cfg.provider
            model.text = cfg.model
            temp.value = cfg.temperature
            maxTokens.value = cfg.maxTokens
        }
        loadAgent(a1Provider, a1Model, a1Temp, a1MaxTokens, s.agent1)
        loadAgent(a2Provider, a2Model, a2Temp, a2MaxTokens, s.agent2)
        loadAgent(orchProvider, orchModel, orchTemp, orchMaxTokens, s.orchestrator)

        maxRounds.value = s.maxRounds
        maxTimeMinutes.value = s.maxTimeMinutes
        maxLlmCalls.value = s.maxLlmCalls
        tokenBudget.value = s.tokenBudget
        llmRequestTimeout.value = s.llmRequestTimeout
        restrictSourceCheckbox.isSelected = s.restrictSourceDeletion
    }

    override fun isModified(): Boolean {
        val s = settings.state
        return pythonPathField.text != s.pythonPath ||
            mcpPortField.text != s.mcpPort.toString() ||
            autoStartCheckbox.isSelected != s.autoStartMcp ||
            String(apiKeyField.password) != (PasswordSafeUtil.getApiKey() ?: "")
    }

    override fun apply() {
        val s = settings.state
        s.pythonPath = pythonPathField.text
        s.mcpPort = mcpPortField.text.toIntOrNull() ?: 8088
        s.autoStartMcp = autoStartCheckbox.isSelected

        val apiKey = String(apiKeyField.password)
        if (apiKey.isNotBlank()) {
            PasswordSafeUtil.setApiKey(apiKey)
        }

        fun saveAgent(
            provider: JComboBox<String>, model: JBTextField,
            temp: JSpinner, maxTokens: JSpinner,
            cfg: SpecEditorSettings.AgentConfig
        ) {
            cfg.provider = provider.selectedItem as? String ?: "openai"
            cfg.model = model.text
            cfg.temperature = (temp.value as? Double) ?: 0.7
            cfg.maxTokens = (maxTokens.value as? Int) ?: 4096
        }
        saveAgent(a1Provider, a1Model, a1Temp, a1MaxTokens, s.agent1)
        saveAgent(a2Provider, a2Model, a2Temp, a2MaxTokens, s.agent2)
        saveAgent(orchProvider, orchModel, orchTemp, orchMaxTokens, s.orchestrator)

        s.maxRounds = (maxRounds.value as? Int) ?: 20
        s.maxTimeMinutes = (maxTimeMinutes.value as? Int) ?: 480
        s.maxLlmCalls = (maxLlmCalls.value as? Int) ?: 30
        s.tokenBudget = (tokenBudget.value as? Int) ?: 50000
        s.llmRequestTimeout = (llmRequestTimeout.value as? Int) ?: 90
        s.restrictSourceDeletion = restrictSourceCheckbox.isSelected
    }

    override fun reset() {
        loadValues()
    }
}

