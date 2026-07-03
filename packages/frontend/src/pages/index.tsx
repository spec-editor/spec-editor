"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import Head from "next/head";
import { useMcp } from "@/mcp/McpContext";
import { useSseEvents } from "@/mcp/useSseEvents";
import { MermaidDiagram } from "@/components/MermaidDiagram";
import { ElementTree } from "@/components/ElementTree";
import { ElementDetail } from "@/components/ElementDetail";
import { ValidationPanel } from "@/components/ValidationPanel";
import { LoadingState, ErrorBanner } from "@/components/ui";

interface MethodologyResult {
  aspects: Array<{ name: string; title: string; default_diagram?: string }>;
}

interface MetricsResult {
  total_elements: number;
  coverage_ratio: number;
  orphan_elements: number;
  cross_aspect_relationships: number;
  aspects: Record<string, number>;
}

// Detect VSCode WebView — hide the left panel to avoid duplicating
// VSCode's built-in tree view.
const isVscodeWebView =
  typeof window !== "undefined" &&
  (window.location.protocol === "vscode-webview:" ||
    typeof (window as any).acquireVsCodeApi !== "undefined");

export default function Home() {
  const mcp = useMcp();
  const [connected, setConnected] = useState(false);
  const [diagramAspect, setDiagramAspect] = useState<string | null>(null);
  const [nodePath, setNodePath] = useState<string | null>(null);
  const [selectedElementId, setSelectedElementId] = useState<string | null>(
    null,
  );
  const [metrics, setMetrics] = useState<MetricsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aspects, setAspects] = useState<string[]>([]);
  const [aspectDefaults, setAspectDefaults] = useState<Record<string, string>>(
    {},
  );
  const [activeDiagramType, setActiveDiagramType] = useState<string>("graph");
  const [validTypes, setValidTypes] = useState<Set<string>>(new Set());
  const [dirty, setDirty] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [showTree, setShowTree] = useState(!isVscodeWebView);
  const [showDetail, setShowDetail] = useState(false);
  const [bottomTab, setBottomTab] = useState<"metrics" | "validation">(
    "metrics",
  );

  const diagramTypes = [
    { name: "graph", label: "Graph" },
    { name: "flowchart", label: "Flowchart" },
    { name: "sequence", label: "Sequence" },
    { name: "class", label: "Class" },
    { name: "er", label: "ER" },
    { name: "state", label: "State" },
    { name: "gantt", label: "Gantt" },
    { name: "pie", label: "Pie" },
    { name: "mindmap", label: "Mind Map" },
    { name: "timeline", label: "Timeline" },
    { name: "sankey", label: "Sankey" },
  ];

  // Read initial element and filters from VSCode/JetBrains bridge
  const win = typeof window !== "undefined" ? (window as any) : null;
  const initElement = win?.INITIAL_ELEMENT || null;
  const initFilterAspect = win?.FILTER_ASPECT || null;
  const initFilterRelation = win?.FILTER_RELATION || null;

  // ── SSE real-time updates ──────────────────────────────────────

  useSseEvents({
    onElementUpdated: useCallback(() => {
      setDirty(true);
    }, []),
    onProjectSwitched: useCallback(() => {
      setRefreshKey((k) => k + 1);
    }, []),
  });

  // ── VSCode/JetBrains bridge: listen for selectElement messages ─

  useEffect(() => {
    if (typeof window === "undefined") return;

    function onMessage(e: MessageEvent) {
      const m = e.data;
      if (!m || m.type !== "specEditor") return;

      if (m.event === "selectElement" && m.elementId) {
        const elId: string = m.elementId;
        if (elId.includes("-") && /^[A-Z]+-/.test(elId)) {
          // Element ID: find its aspect, set as selected
          mcp
            .callToolJson<{ elements: any[] }>("list_all_elements", {})
            .then((list) => {
              const found = (list.elements || []).find(
                (e: any) => e.id === elId,
              );
              if (found) {
                setDiagramAspect(found.aspect);
                setNodePath(elId);
                setSelectedElementId(elId);
                setShowDetail(true);
                setActiveDiagramType(aspectDefaults[found.aspect] || "graph");
              }
            })
            .catch(() => {});
        } else if (aspects.includes(elId)) {
          setDiagramAspect(elId);
          setNodePath(null);
          setActiveDiagramType(aspectDefaults[elId] || "graph");
        }
      } else if (m.event === "elementsChanged") {
        setDirty(true);
      } else if (m.event === "projectLoaded") {
        setRefreshKey((k) => k + 1);
        setDirty(false);
      } else if (m.event === "refreshDiagram") {
        setRefreshKey((k) => k + 1);
      }
    }

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [aspects, mcp]);

  // ── Initialization ─────────────────────────────────────────────

  useEffect(() => {
    (async () => {
      try {
        await mcp.initialize();
        setConnected(true);
        setError(null);

        const method = await mcp.callToolJson<MethodologyResult>(
          "get_methodology",
          {},
        );
        const aspectNames = (method.aspects || []).map((a) => a.name);
        setAspects(aspectNames);

        // Build default diagram map from methodology
        const defaults: Record<string, string> = {};
        for (const a of method.aspects || []) {
          if (a.default_diagram) {
            defaults[a.name] = a.default_diagram;
          }
        }
        setAspectDefaults(defaults);

        // Determine first aspect and its default diagram type
        let firstAspect: string | null = null;
        let initialType = "graph";

        // Set initial aspect from bridge, or first aspect
        if (initElement && aspectNames.includes(initElement)) {
          firstAspect = initElement;
          initialType = defaults[initElement] || "graph";
        } else if (initElement && initElement.includes("-")) {
          try {
            const list = await mcp.callToolJson<{ elements: any[] }>(
              "list_all_elements",
              {},
            );
            const found = (list.elements || []).find(
              (e: any) => e.id === initElement,
            );
            if (found) {
              firstAspect = found.aspect;
              initialType = defaults[found.aspect] || "graph";
              setNodePath(initElement);
            } else {
              firstAspect = aspectNames[0];
              initialType = defaults[aspectNames[0]] || "graph";
            }
          } catch {
            firstAspect = aspectNames[0];
            initialType = defaults[aspectNames[0]] || "graph";
          }
        } else {
          firstAspect = aspectNames[0] || null;
          initialType = firstAspect ? (defaults[firstAspect] || "graph") : "graph";
        }

        setDiagramAspect(firstAspect);
        setActiveDiagramType(initialType);

        const m = await mcp.callToolJson<MetricsResult>("run_metrics", {});
        setMetrics(m);
      } catch {
        setError(
          "MCP server not available. Start: spec-editor mcp --transport http",
        );
      }
    })();
  }, [mcp, initElement]);

  const coveragePct = metrics
    ? Math.round((metrics.coverage_ratio || 0) * 100)
    : 0;
  const aspectBreakdown = metrics?.aspects || {};
  const totalElements = metrics?.total_elements || 0;

  const handleNodeClick = useCallback(
    (nodeId: string) => {
      setNodePath(nodeId);
      setSelectedElementId(nodeId);
      setShowDetail(true);
    },
    [],
  );

  const handleElementSelect = useCallback(
    (elementId: string) => {
      setSelectedElementId(elementId);
      setShowDetail(true);
      // Also focus diagram on this element's aspect
      mcp
        .callToolJson<{ elements: any[] }>("list_all_elements", {})
        .then((list) => {
          const found = (list.elements || []).find(
            (e: any) => e.id === elementId,
          );
          if (found && found.aspect) {
            setDiagramAspect(found.aspect);
            setNodePath(elementId);
          }
        })
        .catch(() => {});
    },
    [mcp],
  );

  const handleDetailNavigate = useCallback(
    (targetId: string) => {
      handleElementSelect(targetId);
    },
    [handleElementSelect],
  );

  // ── Validate diagram types via bridge.js ───────────────────────

  useEffect(() => {
    if (!diagramAspect || !connected) return;
    setValidTypes(new Set());
    const w = window as any;
    if (w._validateDiagramTypes) w._validateDiagramTypes(diagramAspect);
    const iv = setInterval(() => {
      const res = (window as any)._validDiagramTypes || {};
      const ok = diagramTypes.filter(
        (dt) => res[diagramAspect + ":" + dt.name] !== false,
      );
      if (ok.length > 0) {
        setValidTypes(new Set(ok.map((dt) => dt.name)));
      }
    }, 500);
    setTimeout(() => clearInterval(iv), 10000);
    return () => clearInterval(iv);
  }, [diagramAspect, connected]);

  // ── Render ─────────────────────────────────────────────────────

  if (!connected && !error) {
    return (
      <>
        <Head>
          <title>Spec Editor</title>
          <style>{`@keyframes spin{to{transform:rotate(360deg)}}*,:before,:after{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--text);font-family:var(--font)}`}</style>
        </Head>
        <main className="app">
          <LoadingState message="Connecting to MCP server..." />
        </main>
      </>
    );
  }

  return (
    <>
      <Head>
        <title>Spec Editor</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>{`@keyframes spin{to{transform:rotate(360deg)}}*,:before,:after{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--text);font-family:var(--font)}`}</style>
      </Head>

      <main className="app app-layout">
        {error && (
          <div className="error-banner">
            <p>{error}</p>
          </div>
        )}

        {/* ── Top toolbar ─────────────────────────────────── */}
        <div className="toolbar">
          {!isVscodeWebView && (
            <button
              className={`toolbar-btn ${showTree ? "active" : ""}`}
              onClick={() => setShowTree(!showTree)}
              title="Toggle element tree"
            >
              ☰ Tree
            </button>
          )}

          {/* Aspect chips */}
          <div className="aspect-chips">
            {aspects.map((a) => (
              <button
                key={a}
                className={`chip ${diagramAspect === a ? "active" : ""}`}
                onClick={() => {
                  setDiagramAspect(a);
                  setNodePath(null);
                  setActiveDiagramType(aspectDefaults[a] || "graph");
                  setRefreshKey((k) => k + 1);
                }}
              >
                {a.replace(/_/g, " ")}
              </button>
            ))}
          </div>

          <div className="toolbar-spacer" />

          {/* Dirty indicator */}
          {dirty && (
            <button
              className="toolbar-btn toolbar-btn-action"
              onClick={() => {
                setRefreshKey((k) => k + 1);
                setDirty(false);
              }}
              title="Diagram is outdated — click to refresh"
            >
              🔄 Update
            </button>
          )}

          {/* Diagram type selector */}
          <div className="diagram-types">
            {diagramTypes
              .filter((dt) => validTypes.size === 0 || validTypes.has(dt.name))
              .map((dt) => (
                <button
                  key={dt.name}
                  className={`chip chip-type ${activeDiagramType === dt.name ? "active" : ""}`}
                  onClick={() => setActiveDiagramType(dt.name)}
                >
                  {dt.label}
                </button>
              ))}
          </div>
        </div>

        {/* ── Main content: Tree | Diagram | Detail ────────── */}
        <div className="main-content">
          {/* Left panel: Element Tree */}
          {showTree && (
            <div className="panel-left">
              <ElementTree
                onSelectElement={handleElementSelect}
                selectedId={selectedElementId || undefined}
              />
            </div>
          )}

          {/* Center: Diagram */}
          <div className="panel-center">
            <div className="diagram-area">
              {diagramAspect && (
                <MermaidDiagram
                  key={refreshKey}
                  aspect={diagramAspect}
                  diagramType={activeDiagramType}
                  nodePath={nodePath}
                  filterRelation={initFilterRelation}
                  onNodeClick={handleNodeClick}
                />
              )}
              {!diagramAspect && (
                <div className="empty-state">
                  <p>
                    Select an aspect above to generate a diagram, or click an
                    element in the tree.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Right panel: Element Detail */}
          {showDetail && selectedElementId && (
            <div className="panel-right">
              <div className="panel-header">
                <span className="panel-header-title">Element Detail</span>
                <button
                  className="panel-close"
                  onClick={() => setShowDetail(false)}
                >
                  ✕
                </button>
              </div>
              <ElementDetail
                elementId={selectedElementId}
                onNavigate={handleDetailNavigate}
              />
            </div>
          )}
        </div>

        {/* ── Bottom panel: Metrics / Validation ──────────── */}
        <div className="bottom-panel">
          <div className="bottom-tabs">
            <button
              className={`bottom-tab ${bottomTab === "metrics" ? "active" : ""}`}
              onClick={() => setBottomTab("metrics")}
            >
              📊 Metrics
            </button>
            <button
              className={`bottom-tab ${bottomTab === "validation" ? "active" : ""}`}
              onClick={() => setBottomTab("validation")}
            >
              ✅ Validation
            </button>
            <button
              className="bottom-tab-toggle"
              onClick={() => {
                const panel = document.querySelector(".bottom-panel");
                panel?.classList.toggle("collapsed");
              }}
            >
              ▼
            </button>
          </div>

          <div className="bottom-panel-content">
            {bottomTab === "metrics" && metrics && (
              <div className="metrics-inline">
                <div className="metrics-grid">
                  <div className="metric-card">
                    <span className="metric-value">{totalElements}</span>
                    <span className="metric-label">Elements</span>
                  </div>
                  <div className="metric-card">
                    <span className="metric-value">
                      {Object.keys(aspectBreakdown).length}
                    </span>
                    <span className="metric-label">Aspects</span>
                  </div>
                  <div className="metric-card">
                    <span
                      className="metric-value"
                      style={{
                        color:
                          coveragePct >= 80
                            ? "var(--success)"
                            : "var(--warning)",
                      }}
                    >
                      {coveragePct}%
                    </span>
                    <span className="metric-label">Coverage</span>
                  </div>
                  <div className="metric-card">
                    <span
                      className="metric-value"
                      style={{
                        color:
                          (metrics?.orphan_elements || 0) === 0
                            ? "var(--success)"
                            : "var(--error)",
                      }}
                    >
                      {metrics?.orphan_elements || 0}
                    </span>
                    <span className="metric-label">Orphans</span>
                  </div>
                  <div className="metric-card">
                    <span className="metric-value">
                      {metrics?.cross_aspect_relationships || 0}
                    </span>
                    <span className="metric-label">Cross-links</span>
                  </div>
                </div>
              </div>
            )}
            {bottomTab === "validation" && <ValidationPanel />}
          </div>
        </div>
      </main>
    </>
  );
}
