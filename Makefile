# Agentic Model Risk Factory - common commands. Run `make help`.
PY      ?= .venv/bin/python
UV      ?= $(shell command -v uv || echo $$HOME/.local/bin/uv)
MODEL   ?=

.PHONY: help setup setup-r lock test lint run status ui mlflow-ui llm-check spend clean

help:
	@echo "make setup            create .venv (Python 3.11) and install pinned deps + R packages"
	@echo "make test             run the test suite"
	@echo "make lint             ruff lint"
	@echo "make run MODEL=<name> run the pipeline (with human gates) for models/<name>"
	@echo "make status MODEL=<name>  stage/gate status for a model"
	@echo "make ui               Streamlit UI (inventory, gates, audit log, spend)"
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

test:
	LLM_PROVIDER=fake $(PY) -m pytest

lint:
	$(PY) -m ruff check src app tests

run:
	@test -n "$(MODEL)" || (echo "usage: make run MODEL=<name>" && exit 2)
	$(PY) -m factory.cli run --model $(MODEL)

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

clean:
	rm -rf .pytest_cache .ruff_cache
