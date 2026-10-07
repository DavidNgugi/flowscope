.DEFAULT_GOAL := help

BACKEND_DIR := backend
FRONTEND_DIR := frontend
VENV := $(BACKEND_DIR)/.venv
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn

BOLD := \033[1m
DIM := \033[2m
CYAN := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RESET := \033[0m

.PHONY: help
help: ## Show this help
	@echo ""
	@echo "$(BOLD)FlowScope$(RESET) $(DIM)— AI competitor UX/flow analyzer$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "}; \
		/^##@/ {printf "\n$(BOLD)%s$(RESET)\n", substr($$0, 5); next} \
		/^[a-zA-Z0-9_-]+:.*?## / {printf "  $(CYAN)%-16s$(RESET) %s\n", $$1, $$2}' \
		$(MAKEFILE_LIST)
	@echo ""

##@ Setup

.PHONY: setup
setup: setup-backend setup-frontend ## Install all backend + frontend dependencies

.PHONY: setup-backend
setup-backend: ## Create backend venv and install Python dependencies
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(PIP) install --upgrade pip -q
	@$(PIP) install -r $(BACKEND_DIR)/requirements.txt -q
	@test -f $(BACKEND_DIR)/.env || cp $(BACKEND_DIR)/.env.example $(BACKEND_DIR)/.env
	@echo "$(GREEN)✓$(RESET) backend ready — edit $(BACKEND_DIR)/.env to add ANTHROPIC_API_KEY"

.PHONY: setup-frontend
setup-frontend: ## Install frontend npm dependencies
	@cd $(FRONTEND_DIR) && npm install --silent
	@echo "$(GREEN)✓$(RESET) frontend ready"

##@ Local development

.PHONY: dev-backend
dev-backend: ## Run the backend API with auto-reload (needs ffmpeg on PATH)
	@$(UVICORN) app.main:app --reload --port 8000 --app-dir $(BACKEND_DIR)

.PHONY: dev-frontend
dev-frontend: ## Run the frontend dev server
	@cd $(FRONTEND_DIR) && npm run dev

.PHONY: dev
dev: ## Run backend + frontend together (Ctrl+C stops both)
	@trap 'kill 0' EXIT INT TERM; \
	$(UVICORN) app.main:app --reload --port 8000 --app-dir $(BACKEND_DIR) & \
	cd $(FRONTEND_DIR) && npm run dev & \
	wait

##@ Docker

.PHONY: docker-build
docker-build: ## Build backend + frontend Docker images
	docker compose build

.PHONY: docker-up
docker-up: ## Start the stack in Docker (detached)
	docker compose up -d --build
	@echo "$(GREEN)✓$(RESET) backend on http://localhost:8000, frontend on http://localhost:5180"

.PHONY: docker-down
docker-down: ## Stop and remove the Docker stack
	docker compose down

.PHONY: docker-restart
docker-restart: docker-down docker-up ## Restart the Docker stack

.PHONY: docker-logs
docker-logs: ## Tail logs from all Docker services
	docker compose logs -f

.PHONY: docker-ps
docker-ps: ## Show running Docker services
	docker compose ps

##@ Maintenance

.PHONY: health
health: ## Check backend health endpoint (ffmpeg/yt-dlp/API key status)
	@curl -s http://localhost:8000/api/health | python3 -m json.tool

.PHONY: clean
clean: ## Remove venv, node_modules, build artifacts (keeps downloaded media/db)
	rm -rf $(VENV) $(FRONTEND_DIR)/node_modules $(FRONTEND_DIR)/dist
	find $(BACKEND_DIR) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "$(YELLOW)✓$(RESET) cleaned (media/db under backend/data were kept)"

.PHONY: nuke
nuke: clean ## Also delete all downloaded media and the database (irreversible)
	rm -rf $(BACKEND_DIR)/data
	@echo "$(YELLOW)✓$(RESET) all local data deleted"
