# Cloud Run image — Day 10 (docs/13). Playwright needs system Chromium.
# Keep this tag in lockstep with the pinned Playwright driver.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

# Controlled LibreOffice for document previews and PDF output (docs/15
# §LibreOffice): a pinned apt install — never the developer's desktop copy.
# --no-install-recommends keeps it to the conversion essentials.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Resilient installs: pythonhosted/CDN read-timeouts are common in Cloud Build
# and would fail the whole deploy. Longer timeout + retries ride them out.
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=5
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 120 -r requirements.txt
COPY . .
ENV PORT=8080 LIBREOFFICE_CONVERSION_ENABLED=true

# Non-root execution. The base image ships `pwuser` and browsers land in the
# world-readable /ms-playwright, so Chromium and soffice both run unprivileged.
USER pwuser
# --timeout-graceful-shutdown: SSE streams run for up to 55 minutes, and
# uvicorn waits for open connections before running lifespan shutdown. Without
# a bound, Cloud Run SIGKILLs at ~10 s and the browser shutdown/reconcile hook
# never runs whenever the founder has the Browser panel open. Two seconds leaves
# the reviewed 5.5-second browser cleanup budget inside that platform window.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 2"]
