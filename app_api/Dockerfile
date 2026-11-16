# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app_api/ /app/app_api/
COPY config/ /app/config/
COPY prompts/ /app/prompts/
COPY hr_csv/ /app/hr_csv/
COPY employee_profiles.py langgraph_console_skeleton.py llm_config.py vector_search_client.py /app/
COPY vector_service/__init__.py vector_service/chunking.py vector_service/retrieval.py /app/vector_service/
RUN --mount=type=secret,id=hr_data_csv,target=/run/secrets/hr_data.csv,required=true \
    mkdir -p /app/DS-RPC-01/data/hr \
    && install -m 0644 /run/secrets/hr_data.csv /app/DS-RPC-01/data/hr/hr_data.csv

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"

CMD ["uvicorn", "app_api.main:app", "--host=0.0.0.0", "--port=8000"]
