"""OpenAPI 3.x exporter — generates openapi.yaml from api_first methodology.

Converts specification elements (service, endpoint, schema, auth_scheme)
into a valid OpenAPI 3.0 specification document.
"""

from pathlib import Path

import yaml

from src.storage.models import Element


def _get_attr(element: Element, name: str, default: str = "") -> str:
    """Extract an attribute value from element content (heuristic).

    Since attributes are stored in YAML frontmatter, we look for
    'name: value' patterns in the element content.
    """
    content = element.content or ""
    prefix = f"{name}:"
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith(prefix.lower()):
            return stripped[len(prefix) :].strip()
    return default


def build_openapi_spec(
    service: Element,
    endpoints: list[Element],
    schemas: list[Element],
    auth_schemes: list[Element],
) -> dict:
    """Build an OpenAPI 3.0 specification dict from api_first elements.

    Args:
        service: The API Service element
        endpoints: Endpoint elements linked to the service
        schemas: Schema elements
        auth_schemes: Auth scheme elements

    Returns:
        OpenAPI 3.0 spec as a Python dict (ready for YAML serialisation)
    """
    base_url = _get_attr(service, "base_url", "/api/v1")
    api_version = _get_attr(service, "version", "1.0.0")
    api_title = _get_attr(service, "title", service.title)
    api_description = _get_attr(service, "description", service.content)

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": api_title,
            "version": api_version,
        },
        "servers": [
            {"url": base_url},
        ],
        "paths": {},
        "components": {
            "schemas": {},
            "securitySchemes": {},
        },
    }

    if api_description:
        spec["info"]["description"] = api_description

    # Build schemas
    for schema in schemas:
        schema_name = schema.title.replace(" ", "")
        schema_type = _get_attr(schema, "schema_type", "object")
        properties_str = _get_attr(schema, "properties", "")
        required_str = _get_attr(schema, "required_fields", "")

        schema_def = {"type": schema_type}
        if properties_str:
            schema_def["properties"] = {}
            for prop in properties_str.split(","):
                prop = prop.strip()
                if ":" in prop:
                    name, ptype = prop.split(":", 1)
                    schema_def["properties"][name.strip()] = {"type": ptype.strip()}
        if required_str:
            schema_def["required"] = [r.strip() for r in required_str.split(",")]

        spec["components"]["schemas"][schema_name] = schema_def

    # Build auth schemes
    security_list: list[dict] = []
    for auth in auth_schemes:
        auth_type = _get_attr(auth, "auth_type", "bearer")
        scheme_name = auth.title.replace(" ", "")
        scheme_def: dict = {"type": "http"}

        if auth_type == "bearer":
            scheme_def["scheme"] = "bearer"
            scheme_def["bearerFormat"] = "JWT"
        elif auth_type == "oauth2":
            scheme_def["type"] = "oauth2"
            scopes_str = _get_attr(auth, "scopes", "")
            if scopes_str:
                scheme_def["flows"] = {
                    "authorizationCode": {
                        "authorizationUrl": f"{base_url}/oauth/authorize",
                        "tokenUrl": f"{base_url}/oauth/token",
                        "scopes": {s.strip(): s.strip() for s in scopes_str.split(",")},
                    }
                }
        elif auth_type in ("apikey", "apiKey", "api_key"):
            scheme_def["type"] = "apiKey"
            scheme_def["name"] = "X-API-Key"
            in_loc = _get_attr(auth, "in_location", "header")
            scheme_def["in"] = (
                in_loc if in_loc in ("header", "query", "cookie") else "header"
            )

        spec["components"]["securitySchemes"][scheme_name] = scheme_def
        security_list.append({scheme_name: []})

    if security_list:
        spec["security"] = security_list

    # Build paths from endpoints
    for ep in endpoints:
        method = _get_attr(ep, "method", "get").lower()
        path = _get_attr(ep, "path", "/")
        summary = _get_attr(ep, "summary", ep.title)
        description = _get_attr(ep, "description", ep.content)
        operation_id = _get_attr(ep, "operation_id", ep.id.lower().replace("-", "_"))
        tags_str = _get_attr(ep, "tags", "")

        if path not in spec["paths"]:
            spec["paths"][path] = {}

        operation: dict = {
            "summary": summary,
            "operationId": operation_id,
            "responses": {
                "200": {"description": "Successful response"},
            },
        }

        if description:
            operation["description"] = description
        if tags_str:
            operation["tags"] = [t.strip() for t in tags_str.split(",")]

        # Add request body for POST/PUT/PATCH
        if method in ("post", "put", "patch"):
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"type": "object"},
                    }
                },
            }

        spec["paths"][path][method] = operation

    return spec


class OpenAPIExporter:
    """Exports api_first specification elements to openapi.yaml."""

    def export(
        self,
        service: Element,
        endpoints: list[Element],
        schemas: list[Element],
        auth_schemes: list[Element],
        output_path: Path,
    ) -> None:
        """Write OpenAPI 3.0 YAML to output_path.

        Args:
            service: The API Service element
            endpoints: Endpoint elements
            schemas: Schema elements
            auth_schemes: Auth scheme elements
            output_path: Where to write openapi.yaml
        """
        spec = build_openapi_spec(service, endpoints, schemas, auth_schemes)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(
                spec, f, default_flow_style=False, sort_keys=False, allow_unicode=True
            )

    def export_to_string(
        self,
        service: Element,
        endpoints: list[Element],
        schemas: list[Element],
        auth_schemes: list[Element],
    ) -> str:
        """Export to YAML string (for testing)."""
        spec = build_openapi_spec(service, endpoints, schemas, auth_schemes)
        return yaml.dump(
            spec, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
