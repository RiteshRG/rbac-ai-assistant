FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    CHROMA_PERSIST_DIRECTORY=/app/vector_db

WORKDIR /app

COPY vector_service/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY vector_service/ /app/vector_service/
COPY DS-RPC-01/data/engineering/ /app/DS-RPC-01/data/engineering/
COPY DS-RPC-01/data/finance/ /app/DS-RPC-01/data/finance/
COPY DS-RPC-01/data/general/ /app/DS-RPC-01/data/general/
COPY DS-RPC-01/data/marketing/ /app/DS-RPC-01/data/marketing/

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/vector_db \
    && chown -R appuser:appuser /app

USER appuser

RUN for attempt in 1 2 3; do \
        python -m vector_service.ingest_documents && break; \
        if [ "$attempt" -eq 3 ]; then exit 1; fi; \
        echo "Document ingestion failed; retrying ($attempt/3)." >&2; \
        sleep 10; \
    done \
    && python -c "from vector_service.chroma_client import get_document_collection; collection = get_document_collection(); assert collection.count() > 0, 'Pre-ingested Chroma index is missing or empty'"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).read()"

CMD ["uvicorn", "vector_service.app:app", "--host=0.0.0.0", "--port=8000"]
