"use client";
import { useEffect, useState, useMemo, useCallback } from "react";
import { useMcp } from "@/mcp/McpContext";
import { LoadingState, ErrorBanner, EmptyState } from "@/components/ui";
import { STATUS_COLORS, ASPECT_LABELS } from "@/styles/theme";

interface SpecElement {
  id: string;
  aspect: string;
  element_type: string;
  title: string;
  status: string;
  children?: string[];
}
interface Props {
  onSelectElement?: (id: string) => void;
  selectedId?: string;
  className?: string;
}

export function ElementTree({ onSelectElement, selectedId, className }: Props) {
  const mcp = useMcp();
  const [els, setEls] = useState<SpecElement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");

  useEffect(() => {
    let c = false;
    (async () => {
      setLoading(true);
      try {
        const r = await mcp.callToolJson<{ elements: SpecElement[] }>(
          "list_all_elements",
          {},
        );
        if (!c) {
          setEls(r.elements ?? []);
          const f = r.elements?.[0]?.aspect;
          if (f) setExpanded(new Set([f]));
        }
      } catch (e: any) {
        if (!c) setError(e.message);
      } finally {
        if (!c) setLoading(false);
      }
    })();
    return () => {
      c = true;
    };
  }, [mcp]);

  const groups = useMemo(() => {
    const m = new Map<string, SpecElement[]>();
    for (const e of els) {
      const a = e.aspect || "unknown";
      if (!m.has(a)) m.set(a, []);
      m.get(a)!.push(e);
    }
    return [...m.entries()]
      .map(([a, e]) => ({ aspect: a, elements: e }))
      .sort((a, b) => a.aspect.localeCompare(b.aspect));
  }, [els]);

  const filtered = useMemo(() => {
    if (!filter) return groups;
    const lo = filter.toLowerCase();
    return groups
      .map((g) => ({
        ...g,
        elements: g.elements.filter(
          (e) =>
            e.id.toLowerCase().includes(lo) ||
            e.title.toLowerCase().includes(lo),
        ),
      }))
      .filter((g) => g.elements.length > 0);
  }, [groups, filter]);

  const toggle = useCallback(
    (a: string) =>
      setExpanded((p) => {
        const n = new Set(p);
        n.has(a) ? n.delete(a) : n.add(a);
        return n;
      }),
    [],
  );

  if (loading) return <LoadingState message="Loading elements..." />;
  if (error) return <ErrorBanner message={error} />;

  return (
    <div className={`panel tree ${className || ""}`}>
      <div className="tree-search">
        <input
          placeholder="Filter elements..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>
      <div className="tree-list">
        {filtered.length === 0 && <EmptyState message="No elements found" />}
        {filtered.map((g) => (
          <div key={g.aspect} className="aspect-group">
            <button className="aspect-header" onClick={() => toggle(g.aspect)}>
              <span className="aspect-chevron">
                {expanded.has(g.aspect) ? "▼" : "▶"}
              </span>
              <span className="aspect-label">
                {ASPECT_LABELS[g.aspect] || `📦 ${g.aspect}`}
              </span>
              <span className="aspect-count">{g.elements.length}</span>
            </button>
            {expanded.has(g.aspect) &&
              g.elements.map((el) => (
                <button
                  key={el.id}
                  className={`element-item${selectedId === el.id ? " selected" : ""}`}
                  onClick={() => onSelectElement?.(el.id)}
                >
                  <span
                    className="element-dot"
                    style={{
                      background:
                        STATUS_COLORS[el.status] || STATUS_COLORS.draft,
                    }}
                    title={el.status}
                  />
                  <span className="element-id">{el.id}</span>
                  <span className="element-title">{el.title}</span>
                  {el.children?.length ? (
                    <span className="element-children">
                      ({el.children.length})
                    </span>
                  ) : null}
                </button>
              ))}
          </div>
        ))}
      </div>
    </div>
  );
}
