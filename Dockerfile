# Cloud Run image — Day 10 (docs/13). Playwright needs system Chromium.
# NOTE: keep this tag in lockstep with the pinned `playwright` in
# requirements.txt. It is currently BEHIND (base v1.47.0 vs pip 1.62.0), so the
# `playwright install chromium` below downloads a second Chromium and the base
# image's bundled one goes unused. Bump this to v1.62.0-jammy to match and drop
# the redundant download / any browser-vs-driver skew.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

# Controlled LibreOffice for document previews and PDF output (docs/15
# §LibreOffice): a pinned apt install — never the developer's desktop copy.
# --no-install-recommends keeps it to the conversion essentials.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium
COPY . .
ENV PORT=8080

# Non-root execution. The base image ships `pwuser` and browsers land in the
# world-readable /ms-playwright, so Chromium and soffice both run unprivileged.
USER pwuser
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port $PORT"]
