"""Tests for OpenAPI 3.x exporter."""

from pathlib import Path

import yaml

from src.export.openapi_exporter import OpenAPIExporter, build_openapi_spec
from src.storage.models import Element, ElementStatus


def _el(id_: str, title: str, element_type: str, content: str = "", **kw) -> Element:
    defaults = {
        "aspect": "api",
        "status": ElementStatus.CONFIRMED,
        "parent": None,
        "children": [],
        "relationships": {},
        "tags": [],
        "provenance": None,
        "derived_from": [],
        "covered_by": [],
    }
    defaults.update(kw)
    return Element(
        id=id_, title=title, element_type=element_type, content=content, **defaults
    )


class TestBuildOpenAPISpec:
    """Building OpenAPI 3.0 spec from elements."""

    def test_minimal_service(self):
        service = _el(
            "SVC-001",
            "User API",
            "service",
            content="base_url: https://api.example.com/v1\nversion: 1.0.0",
        )
        spec = build_openapi_spec(service, [], [], [])
        assert spec["openapi"] == "3.0.3"
        assert spec["info"]["title"] == "User API"
        assert spec["info"]["version"] == "1.0.0"
        assert spec["servers"][0]["url"] == "https://api.example.com/v1"

    def test_endpoint_get(self):
        service = _el("SVC-001", "API", "service")
        ep = _el(
            "EP-001",
            "List Users",
            "endpoint",
            content="method: GET\npath: /users\nsummary: List all users\ntags: users",
        )
        spec = build_openapi_spec(service, [ep], [], [])
        assert "/users" in spec["paths"]
        assert "get" in spec["paths"]["/users"]
        assert spec["paths"]["/users"]["get"]["summary"] == "List all users"
        assert "users" in spec["paths"]["/users"]["get"]["tags"]

    def test_endpoint_post_with_body(self):
        service = _el("SVC-001", "API", "service")
        ep = _el(
            "EP-002",
            "Create User",
            "endpoint",
            content="method: POST\npath: /users\nsummary: Create user",
        )
        spec = build_openapi_spec(service, [ep], [], [])
        assert "post" in spec["paths"]["/users"]
        assert "requestBody" in spec["paths"]["/users"]["post"]

    def test_schema_object(self):
        service = _el("SVC-001", "API", "service")
        schema = _el(
            "SCH-001",
            "User",
            "schema",
            content="schema_type: object\nproperties: id:integer,name:string,email:string\nrequired_fields: id,name,email",
        )
        spec = build_openapi_spec(service, [], [schema], [])
        assert "User" in spec["components"]["schemas"]
        user_schema = spec["components"]["schemas"]["User"]
        assert user_schema["type"] == "object"
        assert "id" in user_schema["properties"]
        assert user_schema["properties"]["name"]["type"] == "string"
        assert "id" in user_schema["required"]

    def test_bearer_auth(self):
        service = _el("SVC-001", "API", "service")
        auth = _el("AUTH-001", "BearerAuth", "auth_scheme", content="auth_type: bearer")
        spec = build_openapi_spec(service, [], [], [auth])
        assert "BearerAuth" in spec["components"]["securitySchemes"]
        scheme = spec["components"]["securitySchemes"]["BearerAuth"]
        assert scheme["scheme"] == "bearer"

    def test_apikey_auth(self):
        service = _el("SVC-001", "API", "service")
        auth = _el(
            "AUTH-001",
            "ApiKey",
            "auth_scheme",
            content="auth_type: apikey\nin_location: header",
        )
        spec = build_openapi_spec(service, [], [], [auth])
        scheme = spec["components"]["securitySchemes"]["ApiKey"]
        assert scheme["type"] == "apiKey"
        assert scheme["in"] == "header"

    def test_security_applied(self):
        service = _el("SVC-001", "API", "service")
        auth = _el("AUTH-001", "BearerAuth", "auth_scheme", content="auth_type: bearer")
        spec = build_openapi_spec(service, [], [], [auth])
        assert "security" in spec
        assert {"BearerAuth": []} in spec["security"]

    def test_multiple_endpoints(self):
        service = _el("SVC-001", "API", "service")
        ep1 = _el(
            "EP-001", "List Users", "endpoint", content="method: GET\npath: /users"
        )
        ep2 = _el(
            "EP-002", "Get User", "endpoint", content="method: GET\npath: /users/{id}"
        )
        spec = build_openapi_spec(service, [ep1, ep2], [], [])
        assert "/users" in spec["paths"]
        assert "/users/{id}" in spec["paths"]


class TestOpenAPIExporter:
    """File output tests."""

    def test_export_to_file(self, tmp_path):
        service = _el(
            "SVC-001",
            "Test API",
            "service",
            content="base_url: http://localhost\nversion: 1.0.0",
        )
        exporter = OpenAPIExporter()
        output = tmp_path / "openapi.yaml"
        exporter.export(service, [], [], [], output)
        assert output.exists()
        content = output.read_text()
        assert "openapi: 3.0.3" in content
        assert "Test API" in content

    def test_export_to_string(self):
        service = _el("SVC-001", "Mini API", "service")
        exporter = OpenAPIExporter()
        result = exporter.export_to_string(service, [], [], [])
        assert "openapi: 3.0.3" in result
        assert "Mini API" in result

    def test_roundtrip_valid_yaml(self, tmp_path):
        service = _el(
            "SVC-001",
            "Roundtrip API",
            "service",
            content="version: 2.0.0\nbase_url: /v2",
        )
        ep = _el(
            "EP-001",
            "Health",
            "endpoint",
            content="method: GET\npath: /health\nsummary: Health check",
        )
        exporter = OpenAPIExporter()
        output = tmp_path / "openapi.yaml"
        exporter.export(service, [ep], [], [], output)

        # Parse back and verify
        parsed = yaml.safe_load(output.read_text())
        assert parsed["openapi"] == "3.0.3"
        assert "/health" in parsed["paths"]
