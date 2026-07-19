"""Integration tests for code annotation and traceability verification.

Verify the full cycle:
- annotate_code (Python + TypeScript)
- verify_traceability / verify_implements
- parse_typescript (AST-based)
"""

import tempfile
from pathlib import Path

import pytest

from src.mcp.annotator import annotate_code
from src.mcp.parsers.python import parse_python
from src.mcp.parsers.typescript import parse_typescript
from src.mcp.verifier import verify_implements, verify_traceability
from src.storage.models import Element, ElementStatus, ElementSummary

# ======================================================================
# Fake Storage for tests
# ======================================================================


class FakeStorage:
    """Minimal StorageAdapter implementation for tests."""

    def __init__(self, elements: dict[str, Element] | None = None):
        self._elements = elements or {}

    def add(self, el: Element):
        self._elements[el.id] = el

    def read_element(self, element_id: str) -> Element:
        if element_id not in self._elements:
            raise KeyError(element_id)
        return self._elements[element_id]

    def list_all(self) -> list[ElementSummary]:
        return [
            ElementSummary(
                aspect=el.aspect,
                element_type=el.element_type,
                id=el.id,
                title=el.title,
                status=el.status,
                parent=el.parent,
                tags=el.tags,
            )
            for el in self._elements.values()
        ]

    def list_aspect(self, aspect_name: str) -> list[ElementSummary]:
        return [s for s in self.list_all() if s.aspect == aspect_name]

    def write_element(self, element: Element) -> None:
        self._elements[element.id] = element

    def delete_element(self, element_id: str) -> None:
        self._elements.pop(element_id, None)

    def find_related(self, element_id: str) -> list[ElementSummary]:
        return []

    def search(self, query: str) -> list[ElementSummary]:
        return []

    def get_element_path(self, element_id: str) -> str | None:
        return None

    def exists(self, element_id: str) -> bool:
        return element_id in self._elements


def _el(id_: str, title: str, **kw) -> Element:
    """Helper for quick Element creation."""
    defaults = {
        "aspect": "modules",
        "element_type": "api_endpoint",
        "status": ElementStatus.CONFIRMED,
        "parent": None,
        "children": [],
        "relationships": {},
        "tags": [],
        "provenance": None,
        "derived_from": [],
        "covered_by": [],
        "content": f"Requirement: {title}",
    }
    defaults.update(kw)
    return Element(id=id_, title=title, **defaults)


# ======================================================================
# parse_typescript tests (AST-based)
# ======================================================================


class TestParseTypeScript:
    """TypeScript AST parser verification."""

    def _parse(self, code: str):
        with tempfile.NamedTemporaryFile(
            suffix=".ts", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp = f.name
        try:
            annotations, symbols = parse_typescript(Path(tmp))
            return annotations, symbols
        finally:
            Path(tmp).unlink()

    def test_class_with_comment(self):
        code = """
        // @implements("REQ-A")
        export class AuthService {
          login() {}
        }
        """
        annotations, symbols = self._parse(code)
        names = {s.name for s in symbols}
        assert "AuthService" in names
        req_ids = {a.req_id for a in annotations}
        assert "REQ-A" in req_ids

    def test_method_with_decorator(self):
        code = """
        function Implements(id: string) { return function (_: any) {} }

        @Implements("REQ-001")
        class AuthService {
          @Implements("REQ-002")
          async login(user: string, pass: string): Promise<boolean> {
            return true;
          }
        }
        """
        annotations, symbols = self._parse(code)
        names = {s.name for s in symbols}
        assert "login" in names
        # Decorator on method
        req_ids = {a.req_id for a in annotations}
        assert "REQ-002" in req_ids

    def test_export_function_with_comment(self):
        code = """
        /** @implements("REQ-F1") */
        export function validate(input: string): boolean {
          return true;
        }
        """
        annotations, symbols = self._parse(code)
        assert any(s.name == "validate" for s in symbols)
        assert any(a.req_id == "REQ-F1" for a in annotations)

    def test_arrow_function_with_comment(self):
        code = """
        // @implements("REQ-HANDLER")
        export const handler = async (req: any): Promise<any> => {
          return {};
        };
        """
        annotations, symbols = self._parse(code)
        assert any(s.name == "handler" for s in symbols)
        assert any(a.req_id == "REQ-HANDLER" for a in annotations)

    def test_non_decorated_symbols_no_annotations(self):
        code = """
        export function bareFunction(): void {}
        """
        annotations, symbols = self._parse(code)
        names = {s.name for s in symbols}
        assert "bareFunction" in names
        # Without @implements — there should be no annotations
        assert len(annotations) == 0

    def test_case_insensitive_decorator(self):
        code = """
        // @IMPLEMENTS("REQ-LOUD")
        export class LoudService {}
        """
        annotations, symbols = self._parse(code)
        assert any(a.req_id == "REQ-LOUD" for a in annotations)

    def test_javascript_file(self):
        """JS file without types should also parse."""
        code = """
        // @implements("REQ-JS")
        class Calculator {
          add(a, b) {
            return a + b;
          }
        }
        """
        with tempfile.NamedTemporaryFile(
            suffix=".js", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp = f.name
        try:
            annotations, symbols = parse_typescript(Path(tmp))
            assert any(s.name == "Calculator" for s in symbols)
            assert any(a.req_id == "REQ-JS" for a in annotations)
        finally:
            Path(tmp).unlink()

    def test_nested_class_in_function(self):
        """AST should correctly handle nested constructs."""
        code = """
        function factory() {
          // @implements("REQ-INNER")
          class Inner {
            method() {}
          }
        }
        """
        annotations, symbols = self._parse(code)
        names = {s.name for s in symbols}
        assert "factory" in names
        assert "Inner" in names
        assert "method" in names


# ======================================================================
# verify_traceability tests
# ======================================================================


class TestVerifyTraceability:
    """Checking requirement coverage by code."""

    def test_python_coverage(self):
        storage = FakeStorage(
            {
                "MOD-001": _el("MOD-001", "User Auth"),
                "MOD-002": _el("MOD-002", "Payment Gateway"),
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp)
            # File with @implements
            (code_dir / "auth.py").write_text(
                'from src.tracing import implements\n\n@implements("MOD-001")\n'
                "class AuthHandler:\n    pass\n",
                encoding="utf-8",
            )
            # File without @implements
            (code_dir / "utils.py").write_text(
                "def helper():\n    pass\n", encoding="utf-8"
            )

            report = verify_traceability(storage, code_dir, language="python")
            assert report.total_requirements == 2
            assert report.implemented >= 1  # MOD-001 is covered
            assert report.coverage >= 0.5

    def test_typescript_coverage(self):
        storage = FakeStorage(
            {
                "TS-001": _el("TS-001", "TypeScript Auth", aspect="modules"),
                "TS-002": _el("TS-002", "TypeScript Payment", aspect="modules"),
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp)
            (code_dir / "auth.ts").write_text(
                '// @implements("TS-001")\nexport class TSAuth {\n  login() {}\n}\n',
                encoding="utf-8",
            )
            (code_dir / "payment.ts").write_text(
                "export function pay() {}\n", encoding="utf-8"
            )

            report = verify_traceability(storage, code_dir, language="typescript")
            assert report.total_requirements == 2
            assert report.implemented >= 1  # TS-001 is covered

    def test_missing_confirmed_shows_error(self):
        storage = FakeStorage({"REQ-MISS": _el("REQ-MISS", "Missing Requirement")})

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp)
            (code_dir / "other.py").write_text(
                "def unrelated(): pass\n", encoding="utf-8"
            )

            report = verify_traceability(storage, code_dir, language="python")
            assert report.implemented == 0
            # confirmed requirement without implementation -> error gap
            errors = [g for g in report.gaps if g.severity == "error"]
            assert len(errors) >= 1


# ======================================================================
# verify_implements tests (single file)
# ======================================================================


class TestVerifyImplements:
    """Checking a single file for requirement compliance."""

    def test_python_file_with_implements(self):
        storage = FakeStorage({"MOD-001": _el("MOD-001", "User Auth")})

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "auth.py"
            f.write_text(
                'from src.tracing import implements\n\n@implements("MOD-001")\n'
                "class AuthHandler:\n    pass\n",
                encoding="utf-8",
            )

            report = verify_implements(storage, f, language="python")
            assert report.implemented == 1
            assert report.passed is True

    def test_python_file_without_implements(self):
        storage = FakeStorage({})

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "plain.py"
            f.write_text("def hello(): pass\n", encoding="utf-8")

            report = verify_implements(storage, f, language="python")
            assert report.implemented == 0

    def test_typescript_file_with_implements(self):
        storage = FakeStorage({"TS-001": _el("TS-001", "TS Auth")})

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "auth.ts"
            f.write_text(
                '// @implements("TS-001")\nexport class TSAuth {\n  login() {}\n}\n',
                encoding="utf-8",
            )

            report = verify_implements(storage, f, language="typescript")
            assert report.implemented == 1

    def test_missing_file(self):
        storage = FakeStorage({})
        report = verify_implements(
            storage, Path("/nonexistent.ts"), language="typescript"
        )
        assert report.passed is False
        assert any(
            "not found" in g.message.lower() or "not found" in g.message.lower()
            for g in report.gaps
        )

    def test_nonexistent_req_id(self):
        storage = FakeStorage({})

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "orphan.py"
            f.write_text(
                'from src.tracing import implements\n\n@implements("GHOST-999")\n'
                "class Ghost:\n    pass\n",
                encoding="utf-8",
            )

            report = verify_implements(storage, f, language="python")
            # @implements exists, but element missing -> error
            assert any(g.severity == "error" for g in report.gaps)


# ======================================================================
# annotate_code tests (Python)
# ======================================================================


class TestAnnotateCode:
    """Checking automatic code annotation with @implements."""

    def test_dry_run_finds_match(self):
        storage = FakeStorage(
            {
                "MOD-AUTH": _el("MOD-AUTH", "User Authentication"),
                "MOD-PAY": _el("MOD-PAY", "Payment Processor"),
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp)
            (code_dir / "auth.py").write_text(
                "class UserAuthentication:\n    def login(self):\n        pass\n",
                encoding="utf-8",
            )

            result = annotate_code(storage, code_dir, dry_run=True)
            assert result["files_checked"] >= 1
            # "User Authentication" ↔ "UserAuthentication" — 2/2 overlap = 100%
            changes = [
                c for c in result["changes"] if c["symbol"] == "UserAuthentication"
            ]
            assert len(changes) > 0
            assert any(c["req_id"] == "MOD-AUTH" for c in changes)

    def test_dry_run_partial_word_match_limits(self):
        """'authenticator' != 'authentication' — no stemming means these are different words.

        This is a documented limitation of word-level matching.
        "User Auth Service" (3 words) intersected with "UserAuthenticator" (2 words)
        -> only "user" overlaps -> 1/3 = 33% < 50% threshold.
        """
        storage = FakeStorage({"MOD-AUTH": _el("MOD-AUTH", "User Auth Service")})

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp)
            (code_dir / "auth.py").write_text(
                "class UserAuthenticator:\n    pass\n",
                encoding="utf-8",
            )

            result = annotate_code(storage, code_dir, dry_run=True)
            changes = [
                c for c in result["changes"] if c["symbol"] == "UserAuthenticator"
            ]
            # 50% threshold not met — no matches
            assert len(changes) == 0

    def test_dry_run_no_false_positive(self):
        """Short title 'API' should not match everything."""
        storage = FakeStorage({"MOD-API": _el("MOD-API", "API")})

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp)
            (code_dir / "database.py").write_text(
                "class DatabaseConnection:\n    def connect(self):\n        pass\n",
                encoding="utf-8",
            )

            result = annotate_code(storage, code_dir, dry_run=True)
            # "DatabaseConnection" does not contain "api" -> there should be no matches
            changes_db = [
                c for c in result["changes"] if c["symbol"] == "DatabaseConnection"
            ]
            assert len(changes_db) == 0

    def test_dry_run_does_not_write(self):
        storage = FakeStorage({"MOD-AUTH": _el("MOD-AUTH", "User Authentication")})

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp)
            f = code_dir / "auth.py"
            original = "class UserAuthenticator:\n    pass\n"
            f.write_text(original, encoding="utf-8")

            annotate_code(storage, code_dir, dry_run=True)
            # File should NOT change
            assert f.read_text(encoding="utf-8") == original

    def test_real_write_adds_implements(self):
        storage = FakeStorage({"MOD-AUTH": _el("MOD-AUTH", "User Authentication")})

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp)
            f = code_dir / "auth.py"
            f.write_text("class UserAuthenticator:\n    pass\n", encoding="utf-8")

            result = annotate_code(storage, code_dir, dry_run=False)
            assert result["annotated"] >= 1
            content = f.read_text(encoding="utf-8")
            assert '@implements("MOD-AUTH")' in content
            assert "from src.tracing import implements" in content

    def test_already_annotated_skipped(self):
        """File with existing @implements is left untouched."""
        storage = FakeStorage({"MOD-AUTH": _el("MOD-AUTH", "User Auth")})

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp)
            f = code_dir / "auth.py"
            original = (
                'from src.tracing import implements\n\n@implements("MOD-AUTH")\n'
                "class AuthService:\n    pass\n"
            )
            f.write_text(original, encoding="utf-8")

            result = annotate_code(storage, code_dir, dry_run=False)
            assert result["annotated"] == 0
            assert f.read_text(encoding="utf-8") == original

    def test_empty_directory(self):
        storage = FakeStorage({})
        with tempfile.TemporaryDirectory() as tmp:
            result = annotate_code(storage, Path(tmp), dry_run=True)
            assert result["files_checked"] == 0
            assert result["annotated"] == 0

    def test_nonexistent_directory(self):
        storage = FakeStorage({})
        result = annotate_code(storage, Path("/nonexistent"), dry_run=True)
        assert result["files_checked"] == 0
