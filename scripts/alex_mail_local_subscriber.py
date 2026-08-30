#!/usr/bin/env python3
"""Stream Alex Mail Pub/Sub notifications into the local founder app.

The process receives no mailbox content: Gmail publishes only the role-account
address and history cursor.  It forwards the original Pub/Sub envelope to the
existing local webhook and acknowledges only after a successful HTTP response.
The webhook/history cursor and durable event receipts provide idempotency.
"""

from __future__ import annotations

import base64
import json
import os
import signal
import sys
import threading
import urllib.error
import urllib.request

from google.cloud import pubsub_v1


def _configuration() -> tuple[str, str]:
    subscription = os.environ.get("ALEX_MAIL_LOCAL_SUBSCRIPTION", "").strip()
    base_url = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
    if not subscription.startswith("projects/") or "/subscriptions/" not in subscription:
        raise ValueError(
            "ALEX_MAIL_LOCAL_SUBSCRIPTION must be a full Pub/Sub subscription path")
    if base_url != "http://127.0.0.1:8090":
        raise ValueError("local Alex Mail subscriber only forwards to 127.0.0.1:8090")
    return subscription, f"{base_url}/webhooks/alex_mail"


def main() -> int:
    try:
        subscription, webhook = _configuration()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    subscriber = pubsub_v1.SubscriberClient()
    stopped = threading.Event()

    def receive(message) -> None:
        envelope = {
            "message": {
                "messageId": message.message_id,
                "data": base64.b64encode(message.data).decode("ascii"),
                "attributes": dict(message.attributes or {}),
            },
            "subscription": subscription,
        }
        request = urllib.request.Request(
            webhook,
            data=json.dumps(envelope, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if 200 <= response.status < 300:
                    message.ack()
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        message.nack()

    future = subscriber.subscribe(subscription, callback=receive)

    def stop(_signum, _frame) -> None:
        stopped.set()
        future.cancel()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("Alex Mail local event subscriber is ready.")
    try:
        future.result()
    except KeyboardInterrupt:
        stop(signal.SIGINT, None)
    except Exception as exc:
        if not stopped.is_set():
            print(f"Alex Mail local subscriber stopped: {type(exc).__name__}",
                  file=sys.stderr)
            return 1
    finally:
        subscriber.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
