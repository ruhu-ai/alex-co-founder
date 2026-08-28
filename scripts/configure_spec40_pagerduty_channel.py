#!/usr/bin/env python3
"""Create the temporary Spec-40 PagerDuty channel without exposing its key.

The routing key is accepted only from a mode-0600, git-ignored local file. It
is never accepted as a command-line argument, printed, or placed in a child
process environment. The gcloud request body travels over stdin.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

PROJECT = "co-founder-506001"
ACCOUNT = "ijidai@ruhu.ai"
DISPLAY_NAME = "canary failure"
CREDENTIAL_NAME = "PAGERDUTY_ROUTING_KEY"
DEFAULT_CREDENTIAL_FILE = ".env.canary.local"
MANAGED_BY = "cofounder_spec40_canary"
_KEY = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


class ConfigurationError(RuntimeError):
    """Fail-closed local or cloud precondition error."""


def _run(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    child_env = dict(os.environ)
    child_env.pop(CREDENTIAL_NAME, None)
    return subprocess.run(
        args, input=input_text, text=True, capture_output=True,
        check=False, env=child_env)


def _value(*args: str) -> str:
    result = _run(*args)
    if result.returncode:
        raise ConfigurationError(result.stderr.strip()[-500:] or "command failed")
    return result.stdout.strip()


def _credential(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ConfigurationError(
            f"create the regular local credential file {path} first")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ConfigurationError(f"{path} must have mode 0600")
    ignored = _run("git", "check-ignore", "--quiet", str(path))
    if ignored.returncode:
        raise ConfigurationError(f"{path} must be ignored by git")
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name != CREDENTIAL_NAME:
            raise ConfigurationError(
                f"{path} may contain only {CREDENTIAL_NAME}=<routing-key>")
        found.append(value.strip())
    if len(found) != 1 or not _KEY.fullmatch(found[0]):
        raise ConfigurationError(
            "routing key is missing or has an invalid non-secret shape")
    return found[0]


def _existing_channels() -> list[dict[str, Any]]:
    raw = _value(
        "gcloud", "beta", "monitoring", "channels", "list",
        f"--project={PROJECT}", "--format=json")
    value = json.loads(raw or "[]")
    if not isinstance(value, list):
        raise ConfigurationError("notification channel list is unreadable")
    return value


def _verify_context() -> None:
    _value("gcloud", "auth", "print-access-token")
    if _value("gcloud", "config", "get-value", "account") != ACCOUNT:
        raise ConfigurationError(f"active gcloud account must be {ACCOUNT}")
    if _value("gcloud", "config", "get-value", "project") != PROJECT:
        raise ConfigurationError(f"active gcloud project must be {PROJECT}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credential-file", default=DEFAULT_CREDENTIAL_FILE,
        help="ignored mode-0600 file containing PAGERDUTY_ROUTING_KEY")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-project", default="")
    args = parser.parse_args()

    plan = {
        "status": "planned", "project": PROJECT, "account": ACCOUNT,
        "display_name": DISPLAY_NAME, "type": "pagerduty",
        "managed_by": MANAGED_BY, "credential_file": args.credential_file,
        "secret_source": CREDENTIAL_NAME, "secret_value_logged": False,
    }
    if not args.apply:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if args.confirm_project != PROJECT:
        raise ConfigurationError(
            f"--confirm-project must be exactly {PROJECT}")

    _verify_context()
    credential_path = Path(args.credential_file)
    routing_key = _credential(credential_path)
    duplicate = [channel for channel in _existing_channels()
                 if channel.get("displayName") == DISPLAY_NAME]
    if duplicate:
        raise ConfigurationError(
            "a canary failure notification channel already exists; inspect it "
            "instead of creating a duplicate")

    payload = {
        "type": "pagerduty", "displayName": DISPLAY_NAME,
        "description": "Temporary failure destination for the Spec 40 canary.",
        "enabled": True, "labels": {"service_key": routing_key},
        "userLabels": {"managed_by": MANAGED_BY, "purpose": "canary_failure"},
    }
    result = _run(
        "gcloud", "beta", "monitoring", "channels", "create",
        f"--project={PROJECT}", "--channel-content-from-file=/dev/stdin",
        "--format=json", "--quiet", input_text=json.dumps(payload))
    routing_key = ""  # Drop the only Python reference before parsing output.
    payload["labels"].clear()
    if result.returncode:
        raise ConfigurationError(result.stderr.strip()[-500:] or "create failed")
    created = json.loads(result.stdout)
    safe = {
        "status": "created", "name": created.get("name"),
        "display_name": created.get("displayName"),
        "type": created.get("type"), "enabled": created.get("enabled", True),
        "verification_status": created.get("verificationStatus", ""),
        "secret_value_logged": False,
    }
    print(json.dumps(safe, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigurationError as exc:
        raise SystemExit(f"configuration refused: {exc}") from None
