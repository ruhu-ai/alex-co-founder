from pathlib import Path

from services import storage


def test_gcs_artifact_uri_uses_writable_instance_cache(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("ARTIFACT_SERVICE_URI", "gs://example-artifacts")
    monkeypatch.setenv("ARTIFACT_CACHE_DIR", str(cache))
    monkeypatch.setattr(storage, "_ROOT", None)
    monkeypatch.setattr(storage, "_mirror_to_gcs", lambda _name: None)

    storage.save_bytes("synthetic/source.txt", b"bounded")

    assert storage._root() == str(cache)
    assert (cache / "synthetic" / "source.txt").read_bytes() == b"bounded"


def test_gcs_artifact_uri_default_cache_is_not_application_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARTIFACT_SERVICE_URI", "gs://example-artifacts")
    monkeypatch.delenv("ARTIFACT_CACHE_DIR", raising=False)
    monkeypatch.setattr(storage, "_ROOT", None)

    assert storage._root() == "/tmp/cofounder-artifacts"
    assert Path(storage._root()).is_absolute()
