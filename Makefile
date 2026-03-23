PYTHON ?= python
UV ?= uv
APP_MODULE ?= aegisforge.a2a_server:app
HOST ?= 0.0.0.0
PORT ?= 8000
IMAGE_NAME ?= aegisforge:local

.PHONY: help install install-dev lint test smoke run docker-build docker-run verify-repo verify-endpoint clean tree

help:
	@echo "Available targets:"
	@echo "  install          Install runtime dependencies"
	@echo "  install-dev      Install dev dependencies"
	@echo "  lint             Run Ruff"
	@echo "  test             Run pytest"
	@echo "  smoke            Run smoke tests only"
	@echo "  run              Run local uvicorn server"
	@echo "  docker-build     Build Docker image"
	@echo "  docker-run       Run Docker image locally"
	@echo "  verify-repo      Verify repo structure and hygiene"
	@echo "  verify-endpoint  Verify public endpoint"
	@echo "  clean            Remove common local artifacts"
	@echo "  tree             Print repo tree"

install:
	$(PYTHON) -m pip install --upgrade pip
	@if [ -f requirements.txt ]; then $(PYTHON) -m pip install -r requirements.txt; fi
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install --upgrade pip
	@if [ -f requirements-dev.txt ]; then $(PYTHON) -m pip install -r requirements-dev.txt; fi
	@if [ -f requirements.txt ]; then $(PYTHON) -m pip install -r requirements.txt; fi
	$(PYTHON) -m pip install -e .

lint:
	ruff check src tests scripts

test:
	pytest -q

smoke:
	pytest tests/test_smoke -q

run:
	uvicorn $(APP_MODULE) --host $(HOST) --port $(PORT)

docker-build:
	docker build -t $(IMAGE_NAME) .

docker-run:
	docker run --rm -it -p $(PORT):$(PORT) \
		-e HOST=$(HOST) \
		-e PORT=$(PORT) \
		-e AGENT_PORT=$(PORT) \
		-e AEGISFORGE_PUBLIC_URL=http://127.0.0.1:$(PORT) \
		$(IMAGE_NAME)

verify-repo:
	$(PYTHON) scripts/verify_repo.py

verify-endpoint:
	@if [ -z "$$BASE_URL" ]; then echo "Set BASE_URL=http://..."; exit 1; fi
	$(PYTHON) scripts/verify_public_endpoint.py --base-url "$$BASE_URL"

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find . -type d -name "*.egg-info" -prune -exec rm -rf {} +
	rm -rf .venv artifacts

tree:
	$(PYTHON) scripts/print_tree.py --max-depth 4
	