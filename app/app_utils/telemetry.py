"""OpenTelemetry setup (docs/13 §telemetry; matches the reference repo pattern)."""

import logging
import os


def setup_telemetry() -> str | None:
    """Configure GenAI telemetry with GCS upload; NO_CONTENT mode = metadata only."""
    os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")
    bucket = os.environ.get("LOGS_BUCKET_NAME")
    capture_content = os.environ.get(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false"
    )
    if bucket and capture_content != "false":
        logging.info("Prompt-response logging enabled — NO_CONTENT (metadata only)")
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT", "jsonl")
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", "upload")
        os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
        os.environ.setdefault(
            "OTEL_RESOURCE_ATTRIBUTES",
            f"service.namespace=co-founder,service.version={os.environ.get('COMMIT_SHA', 'dev')}",
        )
        path = os.environ.get("GENAI_TELEMETRY_PATH", "completions")
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH", f"gs://{bucket}/{path}"
        )
    else:
        logging.info("Prompt-response logging disabled (set LOGS_BUCKET_NAME to enable)")
    return bucket
