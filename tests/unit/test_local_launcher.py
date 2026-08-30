"""Regression checks for the one-command local runtime contract."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "run_local.sh"


def _copy_launcher(tmp_path: Path, *, with_uvicorn: bool = True) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied = scripts / "run_local.sh"
    shutil.copy2(LAUNCHER, copied)
    if with_uvicorn:
        uvicorn = tmp_path / ".venv" / "bin" / "uvicorn"
        uvicorn.parent.mkdir(parents=True)
        uvicorn.write_text("#!/usr/bin/env bash\nexit 99\n")
        uvicorn.chmod(0o755)
        (uvicorn.parent / "python").symlink_to(sys.executable)
    return copied


def _run(
    script: Path,
    *,
    path: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = path
    env.update(extra_env or {})
    return subprocess.run(
        ["/bin/bash", str(script)],
        text=True,
        capture_output=True,
        timeout=5,
        env=env,
        check=False,
    )


def test_launcher_has_valid_shell_syntax():
    result = subprocess.run(
        ["/bin/bash", "-n", str(LAUNCHER)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_launcher_supports_external_env_and_event_driven_alex_mail():
    source = LAUNCHER.read_text()
    subscriber = (ROOT / "scripts" / "alex_mail_local_subscriber.py").read_text()

    assert 'LOCAL_ENV_FILE="${LOCAL_ENV_FILE:-.env}"' in source
    assert 'LOCAL_ENV_FILE="$ROOT/$LOCAL_ENV_FILE"' in source
    assert "export LOCAL_ENV_FILE" in source
    assert "umask 077" in source
    assert 'VENV_DIR="${LOCAL_VENV_DIR:-.venv}"' in source
    assert "ALEX_MAIL_LOCAL_SUBSCRIPTION" in source
    assert "alex_mail_local_subscriber.py" in source
    assert "SubscriberClient" in subscriber
    assert "message.ack()" in subscriber
    assert "message.nack()" in subscriber
    assert "127.0.0.1:8090/webhooks/alex_mail" not in subscriber


def test_launcher_fails_before_start_when_environment_is_missing(tmp_path):
    script = _copy_launcher(tmp_path, with_uvicorn=False)
    result = _run(script, path="/usr/bin:/bin")
    assert result.returncode == 2
    assert "Missing .venv/bin/uvicorn" in result.stderr


def test_launcher_fails_before_binding_when_adc_is_expired(tmp_path):
    script = _copy_launcher(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    gcloud = fake_bin / "gcloud"
    gcloud.write_text("#!/usr/bin/env bash\nexit 1\n")
    gcloud.chmod(0o755)

    result = _run(script, path=f"{fake_bin}:/usr/bin:/bin")

    assert result.returncode == 2
    assert "Application Default Credentials are missing or expired" in result.stderr
    assert "login" in result.stderr
    assert "exit 99" not in result.stderr


def test_launcher_fails_before_binding_with_broken_crypto_backend(tmp_path):
    script = _copy_launcher(tmp_path)
    python = tmp_path / ".venv" / "bin" / "python"
    python.unlink()
    python.write_text("#!/usr/bin/env bash\nexit 1\n")
    python.chmod(0o755)

    result = _run(script, path="/usr/bin:/bin")

    assert result.returncode == 2
    assert "cryptography/OpenSSL installation is incompatible" in result.stderr
    assert "requirements-dev.txt" in result.stderr


def test_stable_path_invokes_uvicorn_without_empty_array_failure(tmp_path):
    script = _copy_launcher(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    gcloud = fake_bin / "gcloud"
    gcloud.write_text("#!/usr/bin/env bash\nexit 0\n")
    gcloud.chmod(0o755)

    result = _run(
        script,
        path=f"{fake_bin}:/usr/bin:/bin",
        extra_env={"LOCAL_STARTUP_HEALTH_ATTEMPTS": "1"},
    )

    assert result.returncode == 1
    assert "Mock portal" in result.stderr or "Founder app" in result.stderr
    assert "unbound variable" not in result.stderr
