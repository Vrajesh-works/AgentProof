# One-command entry points. `make up` needs Docker; everything else needs uv and Node.
UV ?= uv
BACKEND := backend
FRONTEND := frontend

.PHONY: up down install lint lint-backend lint-frontend test test-unit test-integration test-frontend demo dev-api dev-web gate quilrai

up: ## build and start PostgreSQL, the API and the viewer on http://localhost:8080
	docker compose up --build --wait
	@echo "viewer: http://localhost:8080   api: http://localhost:8000/api/health"

down:
	docker compose down -v

install:
	cd $(BACKEND) && $(UV) sync --all-extras
	cd $(FRONTEND) && npm ci

lint: lint-backend lint-frontend

lint-backend:
	cd $(BACKEND) && $(UV) run ruff check . && $(UV) run ruff format --check .

lint-frontend:
	cd $(FRONTEND) && npm run lint

test: test-unit test-integration test-frontend

test-unit:
	cd $(BACKEND) && $(UV) run pytest -m "not integration"

test-integration:
	cd $(BACKEND) && $(UV) run pytest -m integration

test-frontend:
	cd $(FRONTEND) && npm test

demo: ## seed demo runs into ./backend/agentproof.db (SQLite)
	cd $(BACKEND) && $(UV) run agentproof demo

dev-api:
	cd $(BACKEND) && $(UV) run agentproof serve

dev-web:
	cd $(FRONTEND) && npm run dev

gate: ## score agents/candidate.yaml against agents/baseline.yaml on the shipped suites
	cd $(BACKEND) && for s in retail airline; do \
	  $(UV) run agentproof run ../suites/$$s.yaml --agent ../agents/baseline.yaml --out ../agentproof-results/$$s.base.json --quiet && \
	  $(UV) run agentproof run ../suites/$$s.yaml --agent ../agents/candidate.yaml --out ../agentproof-results/$$s.head.json --quiet && \
	  $(UV) run agentproof gate --base ../agentproof-results/$$s.base.json --head ../agentproof-results/$$s.head.json || exit 1; \
	done

quilrai: ## run the QuilrAI MCP suite; set QUILRAI_MCP_DIR to a QuilrAI_MCP checkout
	cd $(BACKEND) && $(UV) run agentproof run ../suites/quilrai_mcp.yaml
