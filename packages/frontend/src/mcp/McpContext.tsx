/**
 * McpClientContext — React Context for a single MCP client instance.
 *
 * All components share one McpClient instead of creating their own.
 * Wrap your app with <McpProvider> to enable.
 */
"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { McpClient, getMcpUrl, getProjectPath } from "@/mcp/McpClient";

// ── Context ─────────────────────────────────────────────────────────────

const McpContext = createContext<McpClient | null>(null);

export function McpProvider({ children }: { children: ReactNode }) {
  const client = useMemo(
    () => new McpClient(getMcpUrl(), getProjectPath()),
    [],
  );
  return <McpContext.Provider value={client}>{children}</McpContext.Provider>;
}

/** Get the shared MCP client. Throws if used outside <McpProvider>. */
export function useMcp(): McpClient {
  const ctx = useContext(McpContext);
  if (!ctx) {
    throw new Error("useMcp() must be used within <McpProvider>");
  }
  return ctx;
}
