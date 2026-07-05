"""ToolRegistry tests — tool registry."""

import pytest

from src.agents.tools import get_tool_definitions
from src.providers.base import ToolDef


class TestToolRegistry:
    """ToolRegistry: registry with filtering by writable and skills."""

    def test_all_tools_have_unique_names(self):
        """All tools have unique names."""
        tools = get_tool_definitions(writable=True)
        names = [t.name for t in tools]
        assert len(names) == len(set(names)), f"Duplicates: {names}"

    def test_readonly_tools_subset_of_all(self):
        """RO tools are a subset of all tools."""
        all_tools = {t.name for t in get_tool_definitions(writable=True)}
        ro_tools = {t.name for t in get_tool_definitions(writable=False)}
        assert ro_tools.issubset(all_tools)

    def test_write_tools_not_in_readonly(self):
        """Write tools are NOT available in read-only mode."""
        ro_names = {t.name for t in get_tool_definitions(writable=False)}
        write_names = {
            "write_element",
            "delete_element",
            "add_relationship",
            "remove_relationship",
            "report_complete",
            "escalate",
            "request_clarification",
        }
        for name in write_names:
            assert name not in ro_names, f"{name} should not be read-only"

    def test_required_readonly_tools_present(self):
        """Critical RO tools are in place."""
        names = {t.name for t in get_tool_definitions(writable=False)}
        required = {
            "read_element",
            "list_aspect",
            "run_validate",
            "run_metrics",
            "get_methodology",
        }
        assert required.issubset(names), f"Missing: {required - names}"

    def test_code_verification_tools_present(self):
        """New code verification tools are registered in RO."""
        ro_names = {t.name for t in get_tool_definitions(writable=False)}
        required = {"verify_implements", "verify_traceability", "annotate_code"}
        assert required.issubset(ro_names), f"Нет: {required - ro_names}"

    def test_code_verification_tools_not_in_write(self):
        """Verification tools are read-only only."""
        all_tools = get_tool_definitions(writable=True)
        ro_tools = get_tool_definitions(writable=False)
        rw_names = {t.name for t in all_tools} - {t.name for t in ro_tools}
        code_tools = {"verify_implements", "verify_traceability", "annotate_code"}
        for name in code_tools:
            assert name not in rw_names, f"{name} не должен быть write-only"

    def test_verify_implements_schema_has_required_params(self):
        """verify_implements requires code_dir and file_path."""
        tools = {t.name: t for t in get_tool_definitions(writable=False)}
        tool = tools["verify_implements"]
        assert "code_dir" in tool.parameters.get("required", [])
        assert "file_path" in tool.parameters.get("required", [])

    def test_verify_traceability_schema_has_code_dir(self):
        """verify_traceability requires code_dir."""
        tools = {t.name: t for t in get_tool_definitions(writable=False)}
        tool = tools["verify_traceability"]
        assert "code_dir" in tool.parameters.get("required", [])

    def test_annotate_code_schema_has_code_dir(self):
        """annotate_code requires code_dir."""
        tools = {t.name: t for t in get_tool_definitions(writable=False)}
        tool = tools["annotate_code"]
        assert "code_dir" in tool.parameters.get("required", [])

    def test_every_tool_has_valid_json_schema(self):
        """Every tool has a valid JSON schema for parameters."""
        import jsonschema

        for tool in get_tool_definitions(writable=True):
            params = tool.parameters
            assert "type" in params, f"{tool.name}: нет type"
            assert params["type"] == "object", f"{tool.name}: type != object"
            if "required" in params:
                for req in params["required"]:
                    assert req in params.get("properties", {}), (
                        f"{tool.name}: required '{req}' не в properties"
                    )
