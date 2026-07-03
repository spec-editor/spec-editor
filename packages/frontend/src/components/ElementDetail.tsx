"use client";
import { useEffect, useState } from "react";
import { useMcp } from "@/mcp/McpContext";
import { LoadingState, ErrorBanner } from "@/components/ui";
import { STATUS_COLORS } from "@/styles/theme";

interface Relationship {
  role: string;
  target: string;
}
interface SpecElement {
  id: string;
  title: string;
  aspect?: string;
  element_type?: string;
}
interface Link {
  id: string;
  title: string;
}
interface ElementData {
  id: string;
  aspect: string;
  element_type: string;
  title: string;
  status: string;
  content: string;
  parent?: string | null;
  children: string[];
  relationships: Record<string, unknown> | Relationship[];
  derived_from?: string[];
  tags?: string[];
}
interface Props {
  elementId: string;
  onNavigate?: (id: string) => void;
  className?: string;
}

/** Truncate long titles for display. */
function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + "…";
}

export function ElementDetail({ elementId, onNavigate, className }: Props) {
  const mcp = useMcp();
  const [el, setEl] = useState<ElementData | null>(null);
  const [titleById, setTitleById] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let c = false;
    (async () => {
      setLoading(true);
      try {
        const [r, all] = await Promise.all([
          mcp.callToolJson<ElementData>("read_element", {
            element_id: elementId,
          }),
          mcp.callToolJson<{ elements: SpecElement[] }>(
            "list_all_elements",
            {},
          ),
        ]);
        if (c) return;
        setEl(r);
        // Build title lookup
        const lookup: Record<string, string> = {};
        for (const e of all.elements || []) {
          lookup[e.id] = e.title || e.id;
        }
        setTitleById(lookup);
      } catch (e: any) {
        if (!c) setError(e.message);
      } finally {
        if (!c) setLoading(false);
      }
    })();
    return () => {
      c = true;
    };
  }, [elementId, mcp]);

  if (loading) return <LoadingState message={`Loading ${elementId}...`} />;
  if (error) return <ErrorBanner message={error} />;
  if (!el) return null;

  const rels: Relationship[] = Array.isArray(el.relationships)
    ? el.relationships
    : [];
  const parentId = el.parent || undefined;

  // Resolve ID → {id, title}
  function resolve(id: string): Link {
    return { id, title: titleById[id] || id };
  }

  return (
    <div
      className={`panel panel-padded ${className || ""}`}
      style={{ overflow: "auto", maxHeight: "100%" }}
    >
      <div className="detail-header">
        <div className="detail-header-row">
          <span className="detail-id">{el.id}</span>
          <span
            className="detail-status"
            style={{ color: STATUS_COLORS[el.status] || "#888" }}
          >
            {el.status}
          </span>
        </div>
        <h2 className="detail-title">{el.title}</h2>
        <div className="detail-meta">
          <span className="detail-meta-item">
            📂 {el.aspect} / {el.element_type}
          </span>
          {el.tags?.length ? (
            <span className="detail-meta-item">🏷️ {el.tags.join(", ")}</span>
          ) : null}
        </div>
      </div>

      {el.content && (
        <div className="detail-section">
          <h3 className="detail-section-title">Description</h3>
          <div className="detail-content">{el.content}</div>
        </div>
      )}
      {parentId && (
        <LinkSection
          title="Parent"
          links={[resolve(parentId)]}
          onNavigate={onNavigate}
        />
      )}
      {el.children?.length > 0 && (
        <LinkSection
          title={`Children (${el.children.length})`}
          links={el.children.map(resolve)}
          onNavigate={onNavigate}
        />
      )}
      {rels.length > 0 && (
        <div className="detail-section">
          <h3 className="detail-section-title">
            Relationships ({rels.length})
          </h3>
          <div className="detail-link-list">
            {rels.map((rel, i) => {
              const link = resolve(rel.target);
              return (
                <button
                  key={`${rel.role}-${rel.target}-${i}`}
                  className="detail-link"
                  onClick={() => onNavigate?.(rel.target)}
                  title={link.title}
                >
                  <span className="rel-role">{rel.role}</span>
                  <span className="rel-arrow"> → </span>
                  <span className="rel-target-id">{rel.target}</span>
                  <span className="rel-target-title">
                    {truncate(link.title, 40)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      {el.derived_from?.length ? (
        <LinkSection
          title="Derived From"
          links={el.derived_from.map(resolve)}
          onNavigate={onNavigate}
          prefix="📄"
        />
      ) : null}
    </div>
  );
}

function LinkSection({
  title,
  links,
  onNavigate,
  prefix = "📎",
}: {
  title: string;
  links: Link[];
  onNavigate?: (id: string) => void;
  prefix?: string;
}) {
  return (
    <div className="detail-section">
      <h3 className="detail-section-title">{title}</h3>
      <div className="detail-link-list">
        {links.map((link) => (
          <button
            key={link.id}
            className="detail-link"
            onClick={() => onNavigate?.(link.id)}
            title={link.title}
          >
            {prefix} <span className="rel-target-id">{link.id}</span>
            <span className="rel-target-title">
              {truncate(link.title, 50)}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
