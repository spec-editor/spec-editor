package com.speceditor.mcp

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

/**
 * Lightweight MCP JSON-RPC client over HTTP.
 *
 * Communicates with the spec-editor MCP server running as
 * `spec-editor mcp --transport http --port <port>`.
 *
 * Usage:
 * ```
 * val client = McpClient("http://127.0.0.1:5123/mcp")
 * val result = client.callTool("list_projects", mapOf("base_dir" to "/home"))
 * ```
 */
class McpClient(private val serverUrl: String) {

    private val httpClient: HttpClient = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(5))
        .build()

    private var requestId: Int = 0

    // ── Public API ─────────────────────────────────────────────────

    /**
     * Call an MCP tool by name with JSON arguments.
     *
     * Returns the parsed JSON result text.
     * Throws [McpException] on transport or application errors.
     */
    fun callTool(toolName: String, args: Map<String, Any?> = emptyMap()): JsonObject {
        val request = buildJsonRpcRequest("tools/call", mapOf(
            "name" to toolName,
            "arguments" to args
        ))
        return sendRequest(request)
    }

    /**
     * Initialize the MCP session. Returns server info.
     */
    fun initialize(): JsonObject {
        val request = buildJsonRpcRequest("initialize", emptyMap())
        return sendRequest(request)
    }

    /**
     * List all available MCP tools.
     */
    fun listTools(): JsonObject {
        val request = buildJsonRpcRequest("tools/list", emptyMap())
        return sendRequest(request)
    }

    // ── Internal ───────────────────────────────────────────────────

    private fun buildJsonRpcRequest(method: String, params: Map<String, Any?>): String {
        val id = ++requestId
        val paramsJson = params.entries.joinToString(",") { (k, v) ->
            val value = when (v) {
                is String -> "\"${v.replace("\"", "\\\"")}\""
                is Number -> v.toString()
                is Boolean -> v.toString()
                null -> "null"
                else -> "\"$v\""
            }
            "\"$k\": $value"
        }

        return """{"jsonrpc":"2.0","id":$id,"method":"$method","params":{$paramsJson}}"""
    }

    private fun sendRequest(body: String): JsonObject {
        val request = HttpRequest.newBuilder()
            .uri(URI.create(serverUrl))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(body))
            .timeout(Duration.ofSeconds(30))
            .build()

        val response = httpClient.send(request, HttpResponse.BodyHandlers.ofString())
        if (response.statusCode() != 200) {
            throw McpException("HTTP ${response.statusCode()}: ${response.body()}")
        }

        val json = JsonParser.parseString(response.body()).asJsonObject
        val result = json.getAsJsonObject("result") ?: throw McpException("No result in response")

        // Check for MCP-level errors
        if (result.get("isError")?.asBoolean == true) {
            val errorText = result.getAsJsonArray("content")
                ?.get(0)?.asJsonObject
                ?.get("text")?.asString ?: "Unknown MCP error"
            throw McpException(errorText)
        }

        return result
    }
}

/**
 * Exception raised on MCP transport or application errors.
 */
class McpException(message: String) : Exception(message)
