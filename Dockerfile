# Streamlit review UI for the Model Risk Factory (Stage 8). Read-mostly demo image:
# it serves the committed model cards, reports and audit logs. No API key is baked in;
# the UI makes no LLM calls.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src LLM_PROVIDER=fake TRACKING_BACKEND=local

WORKDIR /app
COPY requirements.txt requirements-azure.txt ./
RUN pip install --no-deps -r requirements.txt && pip install --no-deps -r requirements-azure.txt

COPY . .
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
