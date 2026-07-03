/**
 * TypeScript mirror of Python IEditorAdapter.
 *
 * Abstracts editor-specific functionality (VSCode, JetBrains, standalone browser)
 * behind a unified interface. The frontend uses this adapter instead of
 * calling editor APIs directly.
 *
 * References:
 *   SRC-UI-ADAPTER: EditorAdapter for VSCode/Zed/standalone integration
 */

// ── Data types ─────────────────────────────────────────────────────────

export interface GitCommit {
  hash: string;
  author: string;
  date: string;
  message: string;
}

export interface ProjectInfo {
  path: string;
  name: string;
  methodology: string;
  elementCount: number;
}

export interface SCMFileState {
  path: string;
  status: "modified" | "added" | "deleted" | "untracked";
}

export type Disposable = { dispose(): void };

// ── Editor adapter interface ────────────────────────────────────────────

export interface IEditorAdapter {
  /** Human-readable editor name: "standalone", "vscode", "jetbrains", "zed" */
  readonly editorName: string;

  /** Editor version string */
  readonly editorVersion: string;

  // ── Project discovery & switching ────────────────────────────────────

  /** Find spec-editor projects under baseDir (or $HOME/CWD) */
  findProjects(baseDir?: string): Promise<ProjectInfo[]>;

  /** Get the currently active project path */
  getCurrentProject(): Promise<string | null>;

  /** Persist the active project selection */
  setCurrentProject(path: string): Promise<void>;

  /** Subscribe to project changes */
  onProjectChanged(callback: (path: string | null) => void): Disposable;

  // ── File system ──────────────────────────────────────────────────────

  /** Read a text file */
  readFile(path: string): Promise<string>;

  /** Write a text file (creates parent dirs) */
  writeFile(path: string, content: string): Promise<void>;

  /** Delete a file */
  deleteFile(path: string): Promise<void>;

  /** List entries in a directory */
  listDirectory(path: string): Promise<string[]>;

  /** Recursively list all files in a directory */
  walkDirectory(path: string): Promise<string[]>;

  // ── Git ──────────────────────────────────────────────────────────────

  /** Get git history for a file */
  gitHistory(path: string, maxCount?: number): Promise<GitCommit[]>;

  /** Get current unstaged diff for a file */
  gitDiff(path: string): Promise<string>;

  /** List git branches */
  gitBranches(): Promise<string[]>;

  /** Switch to a git branch */
  gitCheckout(branch: string): Promise<void>;

  // ── UI ───────────────────────────────────────────────────────────────

  /** Show informational message */
  showInfo(message: string): void;

  /** Show warning message */
  showWarning(message: string): void;

  /** Show error message */
  showError(message: string): void;

  /** Pick a folder via system dialog */
  pickFolder(title?: string): Promise<string | null>;

  /** Pick a file via system dialog */
  pickFile(title?: string, filters?: Record<string, string[]>): Promise<string | null>;

  // ── Config ───────────────────────────────────────────────────────────

  /** Read editor/project configuration */
  getConfig<T>(key: string, defaultValue?: T): Promise<T>;

  /** Write editor/project configuration */
  setConfig(key: string, value: unknown): Promise<void>;

  // ── Secrets ──────────────────────────────────────────────────────────

  /** Get a secret (e.g. API key) */
  getSecret(key: string): Promise<string | null>;

  /** Set a secret */
  setSecret(key: string, value: string): Promise<void>;

  /** Delete a secret */
  deleteSecret(key: string): Promise<void>;
}
