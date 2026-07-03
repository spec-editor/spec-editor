/**
 * ValidationPanel — specification validation + metrics dashboard.
 */
"use client";

import { useEffect, useState, useMemo } from "react";
import { useMcp } from "@/mcp/McpContext";
import { LoadingState, ErrorBanner } from "@/components/ui";
import { colors, font, spacing, radii } from "@/styles/theme";

interface ValidationIssue {
  severity?: string;
  element_id?: string;
  message?: string;
  field?: string;
  [key: string]: unknown;
}

interface ValidationResult {
  passed: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  issues?: ValidationIssue[];
}

interface MetricsResult {
  total_elements: number;
  coverage_ratio: number;
  orphan_elements: number;
  cross_aspect_relationships: number;
  aspects: Record<string, number>;
  coverage_pct?: number;
  aspect_counts?: Record<string, number>;
}

interface Props {
  className?: string;
}
type Tab = "issues" | "metrics";

export function ValidationPanel({ className }: Props) {
  const mcp = useMcp();
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [metrics, setMetrics] = useState<MetricsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("issues");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const [v, m] = await Promise.all([
          mcp.callToolJson<ValidationResult>("run_validate", {}),
          mcp.callToolJson<MetricsResult>("run_metrics", {}),
        ]);
        if (!cancelled) {
          setValidation(v);
          setMetrics(m);
        }
      } catch (err) {
        if (!cancelled)
          setError(
            err instanceof Error ? err.message : "Failed to run validation",
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mcp]);

  // Normalize server shapes → component expectations
  const allIssues = useMemo(() => {
    if (!validation) return [];
    const errors = validation.errors || [];
    const warnings = validation.warnings || [];
    return [
      ...errors.map((e: ValidationIssue) => ({ ...e, severity: "error" })),
      ...warnings.map((w: ValidationIssue) => ({ ...w, severity: "warning" })),
    ];
  }, [validation]);

  const coveragePct = metrics
    ? Math.round((metrics.coverage_ratio || 0) * 100)
    : 0;
  const orphans = metrics?.orphan_elements ?? 0;
  const crossLinks = metrics?.cross_aspect_relationships ?? 0;
  const aspects = metrics?.aspects ?? metrics?.aspect_counts ?? {};
  const totalElements = metrics?.total_elements ?? 0;

  if (loading) return <LoadingState message="Running validation..." />;
  if (error) return <ErrorBanner message={error} />;
  if (!validation || !metrics) return null;

  const passed = validation.passed;

  return (
    <div className={className} style={S.container}>
      <div
        style={{
          ...S.summary,
          background: passed
            ? colors.interactive.selected
            : "rgba(244, 67, 54, 0.1)",
          borderColor: passed ? colors.accent.success : colors.accent.error,
        }}
      >
        <span style={S.summaryIcon}>{passed ? "✅" : "❌"}</span>
        <span style={S.summaryText}>
          {passed
            ? "All checks passed"
            : `${allIssues.length} issue${allIssues.length !== 1 ? "s" : ""} found`}
        </span>
      </div>

      <div style={S.tabs}>
        {(["issues", "metrics"] as Tab[]).map((t) => (
          <button
            key={t}
            style={{ ...S.tab, ...(activeTab === t ? S.tabActive : {}) }}
            onClick={() => setActiveTab(t)}
          >
            {t === "issues" ? `Issues (${allIssues.length})` : "Metrics"}
          </button>
        ))}
      </div>

      {activeTab === "issues" && (
        <div style={S.list}>
          {allIssues.length === 0 && (
            <div style={S.empty}>
              ✨ No issues — specification is consistent
            </div>
          )}
          {allIssues.map((issue, i) => (
            <div
              key={i}
              style={{
                ...S.issueItem,
                borderLeftColor:
                  issue.severity === "error"
                    ? colors.accent.error
                    : colors.accent.warning,
              }}
            >
              <div style={S.issueHeader}>
                <span
                  style={{
                    ...S.severity,
                    color:
                      issue.severity === "error"
                        ? colors.accent.error
                        : colors.accent.warning,
                  }}
                >
                  {issue.severity === "error" ? "🔴" : "🟡"}{" "}
                  {(issue.severity || "info").toUpperCase()}
                </span>
                {issue.element_id && (
                  <span style={S.issueEid}>{issue.element_id}</span>
                )}
              </div>
              {issue.message && <p style={S.issueMsg}>{issue.message}</p>}
              {issue.field && (
                <span style={S.issueField}>Field: {issue.field}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {activeTab === "metrics" && (
        <div style={S.grid}>
          <MetricCard value={totalElements} label="Total Elements" />
          <MetricCard value={Object.keys(aspects).length} label="Aspects" />
          <MetricCard
            value={`${coveragePct}%`}
            label="Coverage"
            color={
              coveragePct >= 80 ? colors.accent.success : colors.accent.warning
            }
          />
          <MetricCard
            value={orphans}
            label="Orphans"
            color={orphans === 0 ? colors.accent.success : colors.accent.error}
          />
          <MetricCard value={crossLinks} label="Cross-aspect Links" />

          {Object.keys(aspects).length > 0 && (
            <div style={S.breakdown}>
              <h4 style={S.breakdownTitle}>Elements per Aspect</h4>
              {Object.entries(aspects).map(([aspect, count]) => (
                <div key={aspect} style={S.breakdownRow}>
                  <span style={S.breakdownLabel}>{aspect}</span>
                  <div style={S.bar}>
                    <div
                      style={{
                        ...S.barFill,
                        width: `${Math.min(100, (count / totalElements) * 100)}%`,
                      }}
                    />
                  </div>
                  <span style={S.breakdownCount}>{count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MetricCard({
  value,
  label,
  color,
}: {
  value: string | number;
  label: string;
  color?: string;
}) {
  return (
    <div style={S.card}>
      <span style={{ ...S.cardValue, color: color || colors.text.primary }}>
        {value}
      </span>
      <span style={S.cardLabel}>{label}</span>
    </div>
  );
}

const S = {
  container: {
    background: colors.bg.panel,
    borderRadius: radii.xl,
    border: `1px solid ${colors.border.default}`,
    overflow: "hidden",
  },
  summary: {
    display: "flex",
    alignItems: "center",
    gap: spacing.md,
    padding: `${spacing.lg}px ${spacing.xl}px`,
    borderBottom: "1px solid",
  },
  summaryIcon: { fontSize: font.size.xxl },
  summaryText: {
    fontSize: font.size.xl,
    fontWeight: 600,
    color: colors.text.primary,
  },
  tabs: { display: "flex", borderBottom: `1px solid ${colors.border.default}` },
  tab: {
    flex: 1,
    padding: `${spacing.md}px ${spacing.xl}px`,
    background: "none",
    border: "none",
    color: colors.text.muted,
    fontSize: font.size.md,
    fontWeight: 600,
    cursor: "pointer",
    textTransform: "uppercase" as const,
    fontFamily: "inherit",
    borderBottom: "2px solid transparent",
    transition: "all 0.15s",
  },
  tabActive: {
    color: colors.accent.primary,
    borderBottomColor: colors.accent.primary,
  },
  list: { padding: spacing.md, maxHeight: 400, overflowY: "auto" as const },
  issueItem: {
    padding: "10px 12px",
    marginBottom: spacing.sm,
    background: "rgba(255,255,255,0.03)",
    borderRadius: radii.md,
    borderLeft: "3px solid",
  },
  issueHeader: {
    display: "flex",
    alignItems: "center",
    gap: spacing.md,
    marginBottom: 4,
  },
  severity: { fontSize: font.size.sm, fontWeight: 700 },
  issueEid: {
    fontFamily: font.mono,
    fontSize: font.size.md,
    color: colors.accent.primary,
  },
  issueMsg: {
    margin: 0,
    fontSize: font.size.lg,
    color: colors.text.secondary,
    lineHeight: 1.4,
  },
  issueField: {
    fontSize: font.size.sm,
    color: colors.text.dim,
    marginTop: 4,
    display: "inline-block",
  },
  empty: {
    padding: 40,
    textAlign: "center" as const,
    color: colors.accent.success,
  },
  grid: {
    padding: spacing.xl,
    display: "flex",
    flexWrap: "wrap" as const,
    gap: 10,
  },
  card: {
    flex: "1 1 calc(33% - 10px)",
    minWidth: 100,
    background: "rgba(255,255,255,0.03)",
    borderRadius: radii.lg,
    padding: spacing.lg,
    textAlign: "center" as const,
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
  },
  cardValue: { fontSize: 24, fontWeight: 700, color: colors.text.primary },
  cardLabel: {
    fontSize: font.size.sm,
    color: colors.text.muted,
    textTransform: "uppercase" as const,
  },
  breakdown: {
    width: "100%",
    marginTop: spacing.md,
    padding: spacing.lg,
    background: "rgba(255,255,255,0.02)",
    borderRadius: radii.lg,
  },
  breakdownTitle: {
    margin: "0 0 10px 0",
    fontSize: font.size.md,
    color: colors.text.muted,
    textTransform: "uppercase" as const,
  },
  breakdownRow: {
    display: "flex",
    alignItems: "center",
    gap: spacing.md,
    marginBottom: spacing.sm,
  },
  breakdownLabel: {
    fontSize: font.size.sm,
    color: colors.text.tertiary,
    minWidth: 100,
  },
  bar: {
    flex: 1,
    height: 6,
    background: colors.bg.input,
    borderRadius: radii.sm,
    overflow: "hidden",
  },
  barFill: {
    height: "100%",
    background: colors.accent.primary,
    borderRadius: radii.sm,
    transition: "width 0.3s",
  },
  breakdownCount: {
    fontSize: font.size.sm,
    color: colors.text.dim,
    minWidth: 30,
    textAlign: "right" as const,
  },
} satisfies Record<string, React.CSSProperties>;
