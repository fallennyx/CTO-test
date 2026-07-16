# Production image for the PBC Tracker web app (Render / any container host).
FROM python:3.11-slim

# System dependency for OCR of scanned PDFs and photos.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -e '.[parse,llm,report,web]'

# Hosted mode: server-held key is used for everyone; the in-browser key override is disabled,
# and a global spend cap protects the API budget. Override the cap via PBC_MAX_SPEND_USD.
ENV PBC_HOSTED=1 \
    PBC_MAX_SPEND_USD=45

EXPOSE 8000
# Render injects $PORT at runtime; bind 0.0.0.0 so it's reachable.
CMD ["sh", "-c", "uvicorn pbc_agent.webapp.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
