# Spec Editor — monorepo build system
# =============================================================================

VERSION := $(shell cat VERSION)
MCP_PORT := 8088

.PHONY: help dev build test lint clean release

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\n  \033[36m%-24s\033[0m %s\n", "COMMAND", "DESCRIPTION"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2 }' Makefile

# =============================================================================
# Development
# =============================================================================

dev: ## Start MCP server in dev mode (stdio)
	.venv/bin/python -m spec_editor mcp

dev-http: ## Start MCP server over HTTP
	.venv/bin/python -m src.main mcp --transport http --port $(MCP_PORT)

# =============================================================================
# Build
# =============================================================================

build: build-core ## Build all packages

build-core: ## Build Python core package
	.venv/bin/python -m build

build-vscode: build-frontend ## Build VSCode extension (requires npm)
	cd packages/vscode-extension && npm ci && cp -r ../frontend/out ./dist/ && node esbuild.config.js --production

build-zed: ## Build ZED extension (requires cargo)
	cd packages/zed-extension && cargo build --release --target wasm32-wasip1

build-jetbrains: ## Build JetBrains extension (requires JDK 17+)
	cd packages/jetbrains-extension && ./gradlew buildPlugin

build-frontend: ## Build Next.js frontend
	cd packages/frontend && npm ci && STATIC_EXPORT=1 npm run build

dev-frontend: ## Start frontend dev server
	cd packages/frontend && npm run dev

# ==============================================================================
# Test
# =============================================================================

test: ## Run all tests
	.venv/bin/python -m pytest tests/ -v

test-parallel: ## Run all tests in parallel (one worker per CPU core)
	.venv/bin/python -m pytest tests/ -v -n auto --dist loadscope

test-cov: ## Run tests with coverage
	.venv/bin/python -m pytest tests/ -v --cov=src --cov-report=term

test-quick: ## Run only new/changed test files
	.venv/bin/python -m pytest tests/test_editor_adapters.py tests/test_mcp_server.py -v

# =============================================================================
# Lint
# =============================================================================

lint: ## Lint and format check
	.venv/bin/python -m ruff check src/ tests/
	.venv/bin/python -m ruff format --check src/ tests/

format: ## Auto-format code
	.venv/bin/python -m ruff format src/ tests/
	.venv/bin/python -m ruff check --fix src/ tests/

# =============================================================================
# Clean
# =============================================================================

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name spec_editor.egg-info -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/

# =============================================================================
# Docker
# =============================================================================

docker-build: ## Build Docker images
	docker compose build

docker-up: ## Start MCP server + frontend (production)
	docker compose up -d

docker-down: ## Stop all containers
	docker compose down

docker-dev: ## Start in dev mode with hot reload
	docker compose -f docker-compose.dev.yml up

docker-logs: ## View container logs
	docker compose logs -f

docker-clean: ## Remove containers, images, volumes
	docker compose down -v --rmi local

test-e2e-vscode: ## Run VSCode E2E tests (requires VSCode + display)
	cd packages/vscode-extension && npm run test-e2e
