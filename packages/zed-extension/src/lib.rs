// Spec Editor — ZED Extension
//
// Provides slash commands that integrate with the spec-editor MCP server.
// The MCP server is configured as a context_server in ZED settings.
//
// Architecture: ZED WASM extensions can't make HTTP requests directly.
// Instead, MCP tools are accessed through ZED's built-in MCP client
// when the user configures a context_server. This extension provides
// slash commands that guide the user and surface spec content.

use zed_extension_api as zed;

struct SpecEditorExtension;

impl zed::Extension for SpecEditorExtension {
    fn new() -> Self {
        SpecEditorExtension
    }

    // ── Slash commands ──────────────────────────────────────────

    fn run_slash_command(
        &self,
        command: zed::SlashCommand,
        args: Vec<String>,
        worktree: Option<&zed::Worktree>,
    ) -> Result<zed::SlashCommandOutput, String> {
        match command.name.as_str() {
            "spec" => run_spec_command(&args, worktree),
            "spec-diagram" => run_diagram_command(&args),
            "spec-validate" => run_validate_command(&args),
            "spec-search" => run_search_command(&args),
            "spec-context" => run_context_command(&args, worktree),
            _ => Err(format!("Unknown slash command: {}", command.name)),
        }
    }

    fn complete_slash_command_argument(
        &self,
        command: zed::SlashCommand,
        _args: Vec<String>,
    ) -> Result<Vec<zed::SlashCommandArgumentCompletion>, String> {
        match command.name.as_str() {
            "spec" => Ok(vec![
                zed::SlashCommandArgumentCompletion {
                    label: "status".into(),
                    new_text: "status".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "elements".into(),
                    new_text: "elements".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "methodology".into(),
                    new_text: "methodology".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "validate".into(),
                    new_text: "validate".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "metrics".into(),
                    new_text: "metrics".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "sources".into(),
                    new_text: "sources".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "search".into(),
                    new_text: "search".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "element <ID>".into(),
                    new_text: "element ".into(),
                    run_command: false,
                },
            ]),
            "spec-diagram" => Ok(vec![
                zed::SlashCommandArgumentCompletion {
                    label: "modules".into(),
                    new_text: "modules".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "user_scenarios".into(),
                    new_text: "user_scenarios".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "data_entities".into(),
                    new_text: "data_entities".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "ui_states".into(),
                    new_text: "ui_states".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "implementation".into(),
                    new_text: "implementation".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "metrics".into(),
                    new_text: "metrics".into(),
                    run_command: false,
                },
            ]),
            "spec-search" => Ok(vec![
                zed::SlashCommandArgumentCompletion {
                    label: "element:".into(),
                    new_text: "element:".into(),
                    run_command: false,
                },
                zed::SlashCommandArgumentCompletion {
                    label: "code:".into(),
                    new_text: "code:".into(),
                    run_command: false,
                },
            ]),
            "spec-context" => Ok(vec![]),
            _ => Ok(vec![]),
        }
    }
}

// ── Command implementations ──────────────────────────────────────

fn run_spec_command(
    args: &[String],
    worktree: Option<&zed::Worktree>,
) -> Result<zed::SlashCommandOutput, String> {
    let sub = args.first().map(|s| s.as_str()).unwrap_or("status");
    let project = detect_project(worktree);

    let text = match sub {
        "status" => {
            format!(
                "📋 **Spec Editor — Project Status**\n\n\
                 Project: {project}\n\n\
                 **Run these MCP tools to explore:**\n\
                 ```\n\
                 list_all_elements → all elements with count per aspect\n\
                 get_methodology → project methodology + aspects\n\
                 run_metrics → coverage, orphans, connectivity\n\
                 run_validate → spec validation errors/warnings\n\
                 ```\n\n\
                 **Quick commands:**\n\
                 - `/spec elements` — browse all elements\n\
                 - `/spec methodology` — view methodology\n\
                 - `/spec validate` — run validation + show results\n\
                 - `/spec metrics` — show metrics\n\
                 - `/spec sources` — list source documents\n\
                 - `/spec search <query>` — search elements\n\
                 - `/spec element MOD-001` — read specific element"
            )
        }
        "elements" => {
            format!(
                "📋 **Specification Elements**\n\n\
                 Project: {project}\n\n\
                 **Run these MCP tools in order:**\n\
                 1. `list_all_elements` → get all elements grouped by aspect\n\
                 2. `read_element(element_id: \"<ID>\")` → full element with relationships\n\
                 3. `find_related(element_id: \"<ID>\")` → related elements\n\n\
                 **Or browse by aspect:**\n\
                 `list_aspect(aspect: \"<name>\")` → elements in one aspect\n\n\
                 Aspects: sources, modules, user_scenarios, ui_states,\n\
                 data_entities, nfr, implementation, metrics"
            )
        }
        "methodology" => {
            format!(
                "🏗️ **Methodology**\n\n\
                 Project: {project}\n\n\
                 **Run:** `get_methodology` → returns:\n\
                 - Aspect definitions and element types\n\
                 - Relationship types between elements\n\
                 - Validation rules\n\
                 - Built-in methodologies: waterfall, agile, api_first\n\n\
                 **To change methodology:** use `select_methodology` MCP tool."
            )
        }
        "validate" => {
            format!(
                "✅ **Validation**\n\n\
                 Project: {project}\n\n\
                 **Run these MCP tools:**\n\
                 1. `run_validate` → returns errors + warnings array\n\
                 - YAML frontmatter syntax errors\n\
                 - Broken cross-references (parent, children, relationships)\n\
                 - Status inconsistencies\n\
                 - Missing required fields\n\n\
                 2. `run_metrics` → returns:\n\
                 - total_elements, coverage_ratio\n\
                 - orphan_elements, cross_aspect_relationships\n\
                 - element count per aspect\n\n\
                 Show the results in a structured format."
            )
        }
        "metrics" => {
            format!(
                "📊 **Metrics**\n\n\
                 Project: {project}\n\n\
                 **Run:** `run_metrics` → returns JSON:\n\
                 ```json\n\
                 {{\n\
                   \"total_elements\": N,\n\
                   \"coverage_ratio\": 0.0-1.0,\n\
                   \"orphan_elements\": N,\n\
                   \"cross_aspect_relationships\": N,\n\
                   \"aspects\": {{\"modules\": N, ...}}\n\
                 }}\n\
                 ```\n\n\
                 Display coverage %, orphans, and per-aspect breakdown."
            )
        }
        "sources" => {
            format!(
                "📄 **Source Documents**\n\n\
                 Project: {project}\n\n\
                 **Run:** `list_aspect(aspect: \"sources\")` → all source elements\n\
                 Then `read_element(element_id: \"SRC-...\")` for full content.\n\n\
                 Source elements are linked to spec elements via `derived_from`.\n\
                 Use `find_related` to see what was derived from a source."
            )
        }
        "search" => {
            let query = if args.len() > 1 { &args[1..].join(" ") } else { "" };
            if query.is_empty() {
                format!(
                    "🔍 **Search**\n\n\
                     Project: {project}\n\n\
                     **Run:** `search_elements(query: \"<term>\")`\n\
                     Full-text search across all element titles and content.\n\n\
                     **Also:** `search_code(query: \"<term>\")` for codebase grep.\n\n\
                     Provide a search term: `/spec search <query>`"
                )
            } else {
                format!(
                    "🔍 **Search: \"{query}\"**\n\n\
                     Project: {project}\n\n\
                     **Run this MCP tool:**\n\
                     `search_elements(query: \"{query}\")`\n\n\
                     After finding results, use `read_element(element_id: \"<ID>\")`\n\
                     to inspect details and `find_related` to explore connections."
                )
            }
        }
        element_id if element_id.contains('-') && element_id.chars().next().map_or(false, |c| c.is_uppercase()) => {
            format!(
                "🔍 **Element: {element_id}**\n\n\
                 Project: {project}\n\n\
                 **Run this MCP tool:**\n\
                 `read_element(element_id: \"{element_id}\")`\n\
                 → Returns: id, aspect, element_type, title, status, content,\n\
                 parent, children, relationships, derived_from, tags\n\n\
                 **Then explore connections:**\n\
                 `find_related(element_id: \"{element_id}\")`\n\
                 → All elements directly connected via relationships"
            )
        }
        _ => format!(
            "📋 **Spec Editor**\n\n\
             Project: {project}\n\n\
             Available subcommands:\n\
             - `/spec status` — project overview\n\
             - `/spec elements` — browse all elements\n\
             - `/spec methodology` — view methodology\n\
             - `/spec validate` — run validation\n\
             - `/spec metrics` — connectivity metrics\n\
             - `/spec sources` — source documents\n\
             - `/spec search <query>` — search elements\n\
             - `/spec <element-id>` — read specific element (e.g. `/spec MOD-001`)"
        ),
    };

    Ok(zed::SlashCommandOutput {
        text,
        sections: vec![],
    })
}

fn run_diagram_command(args: &[String]) -> Result<zed::SlashCommandOutput, String> {
    let aspect = args.first().map(|s| s.as_str()).unwrap_or("all");

    let (mcp_aspect, label, description) = match aspect {
        "modules" => ("modules", "Modules", "System decomposition into functional modules with dependencies"),
        "user_scenarios" => ("user_scenarios", "User Scenarios", "User journeys and interaction flows"),
        "data_entities" => ("data_entities", "Data Entities", "Entity-relationship model"),
        "ui_states" => ("ui_states", "UI States", "Screen states and navigation graph"),
        "implementation" => ("implementation", "Implementation", "Module implementation dependencies"),
        "metrics" => ("metrics", "Metrics", "Cross-aspect relationship coverage"),
        _ => ("all", "All Aspects", "Complete specification overview"),
    };

    Ok(zed::SlashCommandOutput {
        text: format!(
            "📊 **{label} Diagram**\n\n\
             {description}\n\n\
             **Run this MCP tool:**\n\
             `generate_diagram(aspect: \"{mcp_aspect}\", diagram_type: \"graph\")`\n\
             → Returns Mermaid.js code — paste into mermaid.live to view.\n\n\
             **Diagram types:** graph, flowchart, sequence, class, er, state, gantt, pie\n\
             `list_diagram_types` → all available diagram formats for this aspect."
        ),
        sections: vec![],
    })
}

fn run_validate_command(_args: &[String]) -> Result<zed::SlashCommandOutput, String> {
    Ok(zed::SlashCommandOutput {
        text: format!(
            "🔍 **Specification Validation**\n\n\
             **Run these MCP tools:**\n\
             1. `run_validate` → returns `passed: bool`, `errors: []`, `warnings: []`\n\
             - YAML frontmatter syntax errors\n\
             - Broken cross-references\n\
             - Invalid relationship types\n\
             - Missing required fields\n\
             - Status inconsistencies\n\n\
             2. `run_metrics` → returns:\n\
             - total_elements, coverage_ratio, orphan_elements\n\
             - cross_aspect_relationships, per-aspect counts\n\n\
             **Present results as:**\n\
             - Pass/Fail status with emoji\n\
             - List errors/warnings with element IDs\n\
             - Coverage % and orphan count"
        ),
        sections: vec![],
    })
}

fn run_search_command(args: &[String]) -> Result<zed::SlashCommandOutput, String> {
    let query = args.join(" ");

    if query.is_empty() {
        return Ok(zed::SlashCommandOutput {
            text: format!(
                "🔍 **Search**\n\n\
                 **MCP tools for search:**\n\
                 - `search_elements(query: \"term\")` — full-text search across all elements\n\
                 - `search_code(query: \"term\")` — grep in codebase\n\n\
                 Provide a search term: `/spec-search <query>`"
            ),
            sections: vec![],
        });
    }

    Ok(zed::SlashCommandOutput {
        text: format!(
            "🔍 **Search: \"{query}\"**\n\n\
             **Run this MCP tool:**\n\
             `search_elements(query: \"{query}\")`\n\
             → Returns matching elements with id, title, aspect.\n\n\
             **Then drill down:**\n\
             `read_element(element_id: \"<ID>\")` → full details\n\
             `find_related(element_id: \"<ID>\")` → connections"
        ),
        sections: vec![],
    })
}

fn run_context_command(
    _args: &[String],
    worktree: Option<&zed::Worktree>,
) -> Result<zed::SlashCommandOutput, String> {
    let project = detect_project(worktree);

    Ok(zed::SlashCommandOutput {
        text: format!(
            "🔗 **Spec Editor Context**\n\n\
             Project: {project}\n\n\
             **For code files with @implements annotations:**\n\
             `get_context_for_file(path: \"<filepath>\")`\n\
             → Loads all referenced requirements + related elements.\n\n\
             **Example:**\n\
             ```\n\
             // @implements(\"MOD-001\")\n\
             class AuthService {{ }}\n\
             ```\n\
             → `get_context_for_file(\"src/auth/service.py\")`\n\
             → Returns MOD-001 + all related requirements\n\n\
             **Traceability check:**\n\
             `verify_traceability` → coverage % and gaps.\n\n\
             **Annotate existing code:**\n\
             `annotate_code(code_dir: \"src/\", dry_run: true)`\n\
             → Auto-adds @implements decorators based on symbol names."
        ),
        sections: vec![],
    })
}

// ── Helpers ─────────────────────────────────────────────────────

fn detect_project(worktree: Option<&zed::Worktree>) -> String {
    if let Some(wt) = worktree {
        let root = wt.root_path();
        format!("{root} (current worktree)")
    } else {
        "Not in a worktree — use `switch_project` to select".into()
    }
}

// ── Register extension ───────────────────────────────────────────

zed::register_extension!(SpecEditorExtension);
