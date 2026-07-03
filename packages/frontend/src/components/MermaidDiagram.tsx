"use client";
import { useEffect, useRef, useState, useCallback } from "react";
import { useMcp } from "@/mcp/McpContext";
import { LoadingState, ErrorBanner } from "@/components/ui";

interface Props {
  aspect: string;
  diagramType?: string;
  nodePath?: string | null;
  filterRelation?: string | null;
  className?: string;
  onNodeClick?: (nodeId: string) => void;
}

/**
 * Extract element ID (e.g. MOD-001, SCN-001) from an SVG group node.
 * Navigates up from the click target to find the nearest `<g id="...">` ancestor
 * whose `id` matches the element ID pattern.
 */
function findNodeClick(event: MouseEvent): string | null {
  let el: HTMLElement | null = event.target as HTMLElement | null;
  while (el) {
    if (el.tagName === "g" || el.tagName === "G") {
      const gid = el.getAttribute("id");
      if (gid && /^[A-Z]+-\d+/.test(gid)) {
        return gid;
      }
    }
    el = el.parentElement;
  }
  return null;
}

export function MermaidDiagram({
  aspect,
  diagramType,
  nodePath,
  filterRelation,
  className,
  onNodeClick,
}: Props) {
  const mcp = useMcp();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const onNodeClickRef = useRef(onNodeClick);
  onNodeClickRef.current = onNodeClick;

  // Track mousedown position for click-vs-pan detection
  const mouseStart = useRef<{ x: number; y: number } | null>(null);

  // Event delegation: catch clicks on SVG nodes via container mousedown+mouseup.
  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    mouseStart.current = { x: e.clientX, y: e.clientY };
  }, []);

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!mouseStart.current) return;
    const dx = Math.abs(e.clientX - mouseStart.current.x);
    const dy = Math.abs(e.clientY - mouseStart.current.y);
    mouseStart.current = null;

    if (dx < 4 && dy < 4) {
      const nodeId = findNodeClick(e.nativeEvent);
      if (nodeId && onNodeClickRef.current) {
        onNodeClickRef.current(nodeId);
      }
    }
  }, []);

  useEffect(() => {
    console.log("[MermaidDiagram] useEffect START", { aspect, nodePath });
    let c = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const diagramEngine =
          (window as any).__SPEC_EDITOR_DIAGRAM_ENGINE__ || "template";
        const relationScope =
          (window as any).__SPEC_EDITOR_RELATION_SCOPE__ || "";
        const toolName =
          diagramEngine === "local_llm"
            ? "generate_local_diagram"
            : "generate_diagram";

        const args: Record<string, unknown> = {
          aspect,
        };
        if (toolName === "generate_diagram") {
          args.diagram_type = diagramType || "flowchart";
        }
        if (nodePath) args.node_path = nodePath;
        if (relationScope) args.relation_scope = relationScope;
        const r = (await mcp.callTool(toolName, args)) as any;
        console.log(
          `[MermaidDiagram] ${toolName} response keys:`,
          Object.keys(r || {}).join(","),
        );
        if (c) return;
        console.log(
          "[MermaidDiagram] r.content type:",
          typeof r.content,
          Array.isArray(r.content) ? "array" : "not array",
        );
        let src = r.content?.[0]?.text;
        console.log("[MermaidDiagram] src:", src?.substring(0, 50));
        if (!src) {
          setError("Empty diagram response");
          return;
        }
        if (src.startsWith("{")) {
          try {
            const p = JSON.parse(src);
            src = p.diagram || p.mermaid || src;
            console.log(
              "[MermaidDiagram] parsed JSON, diagram:",
              src.substring(0, 80),
            );
          } catch {
            console.log("[MermaidDiagram] JSON parse failed");
          }
        }

        console.log(
          "[MermaidDiagram] calling mermaid.render with",
          src.substring(0, 80),
        );

        const vscodeMode = !!(window as any).loadMermaid;
        const mermaid = vscodeMode
          ? await new Promise<any>((resolve) =>
              (window as any).loadMermaid(resolve),
            )
          : (await import("mermaid")).default;

        if (!vscodeMode) {
          mermaid.initialize({
            startOnLoad: false,
            theme: "dark",
            maxEdges: 3000,
            maxTextSize: 90000,
            securityLevel: "loose",
            themeVariables: {
              primaryColor: "#4A90D9",
              primaryTextColor: "#eee",
              lineColor: "#888",
              secondaryColor: "#16213e",
              tertiaryColor: "#1a1a2e",
            },
          });
        }

        // Insert SVG into container. The container div is always in DOM
        // (hidden during loading) so containerRef.current is never null.
        setLoading(false); // unhide container BEFORE inserting SVG
        // Wait for React to flush the state update so containerRef is attached
        await new Promise((r) => setTimeout(r, 0));

        const container = containerRef.current;
        if (!container || c) return;

        const { svg: s } = await mermaid.render(`mermaid-${aspect}`, src);
        console.log("[MermaidDiagram] SVG rendered, length:", s.length);

        if (c) return;
        container.innerHTML = s;
      } catch (e: any) {
        if (!c) setError(e.message);
      }
    })();
    return () => {
      c = true;
    };
  }, [aspect, diagramType, nodePath, mcp]);

  // Animate diagram transitions
  var switchingClass = loading ? " switching" : "";

  if (error)
    return (
      <ErrorBanner
        message={error}
        hint="Make sure the MCP server is running: spec-editor mcp --transport http"
      />
    );
  return (
    <>
      {loading && <LoadingState message={`Generating ${aspect} diagram...`} />}
      <div
        ref={containerRef}
        className={`panel panel-padded diagram-container${switchingClass} ${className || ""}`}
        style={{ display: loading ? "none" : "block" }}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
      />
    </>
  );
}
