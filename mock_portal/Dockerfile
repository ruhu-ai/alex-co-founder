# Mock portal image (docs/13) — lightweight, no browser inside.
FROM python:3.11-slim
WORKDIR /app
# Longer timeout + retries: a transient pythonhosted read-timeout in Cloud Build
# should not fail the deploy (it did once — a flake, not a code issue).
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=5
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 120 -r requirements.txt
COPY . .
ENV PORT=8080
RUN useradd --create-home --uid 10001 portal
USER portal
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
