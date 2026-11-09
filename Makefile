# Agentic Model Risk Factory - common commands. Run `make help`.
PY      ?= .venv/bin/python
UV      ?= $(shell command -v uv || echo $$HOME/.local/bin/uv)
MODEL   ?=

.PHONY: help setup setup-r lock data legacy test lint run run-all monitor benchmark inventory status ui mlflow-ui llm-check spend docker-build docker-run infra-check clean

help:
	@echo "make setup            create .venv (Python 3.11) and install pinned deps + R packages"
	@echo "make data             download public datasets and build fixed train/test splits"
	@echo "make legacy           run the legacy models and save reference outputs"
	@echo "make test             run the test suite"
	@echo "make lint             ruff lint"
	@echo "make run MODEL=<name> run the pipeline (with human gates) for models/<name>"
	@echo "make run-all          run every model in models/ up to its next gate (no prompts)"
	@echo "make monitor MODEL=<name>  drift monitoring; a breach triggers re-validation (Gate 4)"
	@echo "make benchmark            time the pipeline per model vs config/manual_baseline.yaml"
	@echo "make inventory        rebuild the inventory table + dependency map from model cards"
	@echo "make status MODEL=<name>  stage/gate status for a model"
	@echo "make ui               Streamlit UI (inventory, gates, reports, drift, audit, spend)"
	@echo "make docker-run       build the UI image and serve it on :8501 (Stage 8)"
	@echo "make infra-check      offline Bicep build + shellcheck of infra/ scripts"
	@echo "make mlflow-ui        MLflow UI on the local tracking store"
	@echo "make llm-check        verify configured Claude model IDs (free API call)"
	@echo "make spend            LLM token/cost summary"

setup:
	$(UV) venv --python 3.11 .venv
	$(UV) pip install --python $(PY) -r requirements.txt
	$(UV) pip install --python $(PY) --no-deps -e .
	$(MAKE) setup-r

setup-r:
	Rscript r/install_packages.R

lock:
	$(UV) pip compile --python-version 3.11 requirements.in -o requirements.txt

data:
	$(PY) -m factory.data

legacy:
	Rscript models/credit_scorecard/scorecard.R
	$(PY) models/fraud_gbm/train_fraud.py
	$(PY) models/pricing_glm/pricing_glm.py

test:
	LLM_PROVIDER=fake $(PY) -m pytest

lint:
	$(PY) -m ruff check src app tests

run:
	@test -n "$(MODEL)" || (echo "usage: make run MODEL=<name>" && exit 2)
	$(PY) -m factory.cli run --model $(MODEL)

run-all:
	@for d in models/*/; do m=$$(basename $$d); echo "== $$m"; \
	  $(PY) -m factory.cli run --model $$m --no-prompt || true; done

monitor:
	@test -n "$(MODEL)" || (echo "usage: make monitor MODEL=<name>" && exit 2)
	$(PY) -m factory.cli monitor --model $(MODEL)

benchmark:
	$(PY) -m factory.cli benchmark

inventory:
	$(PY) -m factory.cli inventory

status:
	$(PY) -m factory.cli status --model $(MODEL)

ui:
	$(PY) -m streamlit run app/streamlit_app.py

mlflow-ui:
	$(PY) -m mlflow ui --backend-store-uri artifacts/mlruns --port 5000

llm-check:
	$(PY) -m factory.cli llm-check

spend:
	$(PY) -m factory.cli spend

docker-build:
	docker build -t model-risk-factory:local .

docker-run: docker-build
	docker run --rm -p 8501:8501 model-risk-factory:local

infra-check:
	bicep build infra/main.bicep --stdout > /dev/null
	shellcheck infra/deploy.sh infra/teardown.sh

clean:
	rm -rf .pytest_cache .ruff_cache
