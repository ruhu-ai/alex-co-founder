"""Credential-free import contract for the shared agent configuration."""

from google.auth import exceptions as auth_exceptions

from agents.co_founder import config


def test_missing_adc_does_not_break_application_import(monkeypatch):
    """CI and local UI-only work must not authenticate during collection."""

    def missing_credentials():
        raise auth_exceptions.DefaultCredentialsError("no credentials")

    monkeypatch.setattr(config.google.auth, "default", missing_credentials)
    assert config._discover_default_project_id() == ""


def test_adc_project_is_discovered_when_available(monkeypatch):
    monkeypatch.setattr(
        config.google.auth, "default", lambda: (object(), "project-from-adc"))
    assert config._discover_default_project_id() == "project-from-adc"
