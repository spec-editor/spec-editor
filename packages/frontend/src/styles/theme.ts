/**
 * Shared design tokens and constants for the Spec Editor frontend.
 *
 * Single source of truth for colors, spacing, typography, and
 * status/aspect mappings used across all components.
 */

// ── Color palette ───────────────────────────────────────────────────────

export const colors = {
  bg: {
    page: "#0f0f1a",
    panel: "#16213e",
    input: "#1a1a2e",
  },
  border: {
    default: "#333",
    subtle: "#222",
    accent: "#4A90D9",
    separator: "#2a2a4a",
  },
  text: {
    primary: "#eee",
    secondary: "#ccc",
    tertiary: "#aaa",
    muted: "#888",
    dim: "#666",
    subtle: "#555",
    placeholder: "#444",
    error: "#faa",
  },
  accent: {
    primary: "#4A90D9",
    success: "#4CAF50",
    warning: "#FFA726",
    error: "#f44336",
  },
  interactive: {
    hover: "rgba(74, 144, 217, 0.08)",
    selected: "rgba(74, 144, 217, 0.15)",
    errorBg: "#3e1a1a",
  },
} as const;

// ── Spacing / Radii / Typography ─────────────────────────────────────────

export const spacing = {
  xs: 4,
  sm: 6,
  md: 8,
  lg: 12,
  xl: 16,
  xxl: 20,
} as const;

export const radii = { sm: 3, md: 4, lg: 6, xl: 8, pill: 16 } as const;

export const font = {
  sans: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  mono: "monospace",
  size: { xs: 10, sm: 11, md: 12, lg: 13, xl: 14, xxl: 18, title: 24 },
} as const;

// ── Status mappings ──────────────────────────────────────────────────────

export const STATUS_COLORS: Record<string, string> = {
  draft: colors.text.muted,
  reviewed: colors.accent.primary,
  confirmed: colors.accent.success,
  deprecated: colors.accent.error,
};

export const ASPECT_LABELS: Record<string, string> = {
  sources: "📄 Sources",
  modules: "🧩 Modules",
  user_scenarios: "👤 User Scenarios",
  user_interface: "🖥️ UI States",
  ui_states: "🖥️ UI States",
  data_entities: "🗄️ Data Entities",
  nfr: "⚡ Non-Functional",
  non_functional: "⚡ Non-Functional",
  implementation: "🔧 Implementation",
  metrics: "📊 Metrics",
};
