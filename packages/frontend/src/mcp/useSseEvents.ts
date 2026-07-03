/**
 * SSE (Server-Sent Events) hook for real-time updates from MCP server.
 *
 * Subscribes to /events endpoint and calls callbacks for:
 * - element_updated
 * - project_switched
 * - diagram_generated
 */

import { useEffect, useRef } from "react";
import { getSseUrl } from "@/mcp/McpClient";

export interface SseCallbacks {
  onElementUpdated?: (data: {
    action: string;
    elementId: string;
    aspect: string;
  }) => void;
  onProjectSwitched?: (data: { project: string; prevProject: string }) => void;
  onDiagramGenerated?: (data: { aspect: string; diagram_type: string }) => void;
  onConnected?: () => void;
  onError?: (error: Event) => void;
}

export function useSseEvents(callbacks: SseCallbacks) {
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  useEffect(() => {
    // Skip SSE in VSCode WebView — not available through postMessage bridge
    if (
      typeof window !== "undefined" &&
      window.location.protocol === "vscode-webview:"
    ) {
      return;
    }
    const url = getSseUrl();
    const eventSource = new EventSource(url);

    eventSource.addEventListener("connected", () => {
      callbacksRef.current.onConnected?.();
    });

    eventSource.addEventListener("element_updated", (event: MessageEvent) => {
      const data = JSON.parse(event.data);
      callbacksRef.current.onElementUpdated?.(data);
    });

    eventSource.addEventListener("project_switched", (event: MessageEvent) => {
      const data = JSON.parse(event.data);
      callbacksRef.current.onProjectSwitched?.(data);
    });

    eventSource.addEventListener("diagram_generated", (event: MessageEvent) => {
      const data = JSON.parse(event.data);
      callbacksRef.current.onDiagramGenerated?.(data);
    });

    eventSource.addEventListener("error", (event: Event) => {
      callbacksRef.current.onError?.(event);
    });

    return () => {
      eventSource.close();
    };
  }, []);
}
