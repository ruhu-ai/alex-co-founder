"""Document output tests (docs/15): builders, validation gate, registry."""

import os

import pytest

from services import document_service as ds

pytestmark = pytest.mark.asyncio


@pytest.fixture
def artifacts_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr("services.storage._root", lambda: str(tmp_path))
    return tmp_path


class TestLibreOfficePosture:
    """docs/15 §LibreOffice: conversion hardening contracts."""

    def test_native_macos_conversion_is_disabled_unless_explicitly_enabled(
            self, monkeypatch):
        monkeypatch.delenv("LIBREOFFICE_CONVERSION_ENABLED", raising=False)
        monkeypatch.setattr(ds.sys, "platform", "darwin")

        assert ds._libreoffice_conversion_enabled() is False
        assert ds._soffice() is None

    def test_disabled_conversion_never_spawns_a_process(
            self, artifacts_tmp, monkeypatch):
        import subprocess

        src = artifacts_tmp / "local.docx"
        src.write_bytes(b"local")
        monkeypatch.setenv("LIBREOFFICE_CONVERSION_ENABLED", "false")

        def _must_not_spawn(*_args, **_kwargs):
            raise AssertionError("disabled conversion attempted to launch LibreOffice")

        monkeypatch.setattr(subprocess, "Popen", _must_not_spawn)
        result = ds.convert_to_pdf(str(src), str(artifacts_tmp / "local.pdf"))

        assert result["status"] == "error"
        assert "disabled" in result["message"]

    def test_macro_parts_stripped_from_ooxml(self, artifacts_tmp):
        import zipfile

        src = artifacts_tmp / "evil.docx"
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("word/document.xml", "<doc>body</doc>")
            z.writestr("word/vbaProject.bin", b"MALWARE")
            z.writestr("word/activeX1.bin", b"X")
        dst = artifacts_tmp / "clean.docx"
        ds._strip_ooxml_macros(str(src), str(dst))
        with zipfile.ZipFile(dst) as z:
            names = z.namelist()
        assert "word/document.xml" in names
        assert not any("vbaProject" in n or "activeX" in n.lower() for n in names)

    def test_size_cap_is_error_data(self, artifacts_tmp, monkeypatch):
        monkeypatch.setenv("LIBREOFFICE_CONVERSION_ENABLED", "true")
        big = artifacts_tmp / "big.docx"
        big.write_bytes(b"0" * 1024)
        monkeypatch.setattr(ds, "MAX_CONVERT_BYTES", 100)
        result = ds.convert_to_pdf(str(big), str(artifacts_tmp / "big.pdf"))
        assert result["status"] == "error" and result["error"] is True
        assert "too large" in result["message"]
        assert not (artifacts_tmp / "big.pdf").exists()

    def test_missing_source_is_error_data(self, artifacts_tmp, monkeypatch):
        monkeypatch.setenv("LIBREOFFICE_CONVERSION_ENABLED", "true")
        result = ds.convert_to_pdf(
            str(artifacts_tmp / "nope.docx"), str(artifacts_tmp / "nope.pdf"))
        assert result["status"] == "error" and result["error"] is True

    def test_timeout_kills_the_entire_conversion_process_group(
            self, artifacts_tmp, monkeypatch):
        import signal
        import subprocess

        src = artifacts_tmp / "slow.docx"
        src.write_bytes(b"not-an-ooxml-package")
        killed = []

        class _TimedOutProcess:
            pid = 4242
            returncode = None

            def __init__(self):
                self.communications = 0

            def communicate(self, timeout=None):
                self.communications += 1
                if self.communications == 1:
                    raise subprocess.TimeoutExpired("soffice", timeout)
                return b"", b""

            def kill(self):
                killed.append((self.pid, "fallback"))

        process = _TimedOutProcess()
        monkeypatch.setenv("LIBREOFFICE_CONVERSION_ENABLED", "true")
        monkeypatch.setattr(ds, "_soffice", lambda: "/fake/soffice")
        monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: process)
        monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))
        monkeypatch.setattr(ds, "_CONVERT_TIMEOUT_S", 0.01)

        result = ds.convert_to_pdf(str(src), str(artifacts_tmp / "slow.pdf"))

        assert result["status"] == "error" and "timed out" in result["message"]
        assert killed == [(4242, signal.SIGKILL)]
        assert process.communications == 2

    @pytest.mark.skipif(not ds._soffice(), reason="soffice not installed")
    def test_real_conversion_validates_output_and_cleans_up(self, artifacts_tmp):
        import glob
        import tempfile

        built = ds.produce("docx", "Posture", {"sections": [
            {"heading": "A", "paragraphs": ["Hello"]}]}, "posture.docx")
        assert built["status"] == "success"
        result = ds.convert_to_pdf(
            os.path.join(str(artifacts_tmp), "posture.docx"),
            str(artifacts_tmp / "posture.pdf"))
        assert result["status"] == "success"
        assert ds.validate_document(str(artifacts_tmp / "posture.pdf"), "pdf")[
            "status"] == "success"
        leftovers = [d for d in glob.glob(
            os.path.join(tempfile.gettempdir(), "cofounder-soffice-*"))
            if os.path.basename(d) != "cofounder-soffice-profile"]  # legacy
        assert leftovers == []


class TestBuilders:
    def test_docx_valid(self, artifacts_tmp):
        r = ds.produce("docx", "Test Pack",
                       {"sections": [{"heading": "Traction",
                                      "paragraphs": ["1,200 patients."],
                                      "bullets": ["3 clinics", "pre-revenue"]}]},
                       "test_v1.docx")
        assert r["status"] == "success"
        assert os.path.getsize(artifacts_tmp / "test_v1.docx") > 0

    def test_xlsx_with_formula(self, artifacts_tmp):
        r = ds.produce("xlsx", "Budget",
                       {"sheets": [{"name": "Budget", "columns": ["Item", "Amount"],
                                    "rows": [["Venue", 1000]],
                                    "formulas": {"B3": "=SUM(B2:B2)"}}]},
                       "budget_v1.xlsx")
        assert r["status"] == "success"
        from openpyxl import load_workbook
        wb = load_workbook(artifacts_tmp / "budget_v1.xlsx")
        assert wb["Budget"]["B3"].value == "=SUM(B2:B2)"

    def test_pptx_with_notes(self, artifacts_tmp):
        r = ds.produce("pptx", "Deck",
                       {"slides": [{"title": "Problem", "bullets": ["a", "b"],
                                    "notes": "speaker note"}]},
                       "deck_v1.pptx")
        assert r["status"] == "success"

    def test_bad_spec_is_error_data_no_artifact(self, artifacts_tmp):
        r = ds.produce("docx", "Broken", {"sections": "not-a-list"}, "bad_v1.docx")
        assert r["status"] == "error" and r["error"] is True
        assert not (artifacts_tmp / "bad_v1.docx").exists()  # no partial artifact

    def test_unknown_kind_rejected(self, artifacts_tmp):
        assert ds.produce("pdf", "x", {"sections": []}, "x.pdf")["status"] == "error"
        assert ds.produce("txt", "x", {}, "x.txt")["status"] == "error"

    def test_pdf_build_and_validate(self, artifacts_tmp):
        if not ds._soffice():
            pytest.skip("LibreOffice conversion unavailable in this environment")
        r = ds.produce("pdf", "Pack", {"sections": [{"heading": "Traction",
                                                    "paragraphs": ["1,200 patients."]}]},
                       "pack_v1.pdf")
        assert r["status"] == "success"
        with open(artifacts_tmp / "pack_v1.pdf", "rb") as fh:
            assert fh.read(5) == b"%PDF-"

    def test_pdf_validation_rejects_garbage(self, artifacts_tmp):
        garbage = artifacts_tmp / "garbage.pdf"
        garbage.write_bytes(b"definitely not a pdf")
        assert ds.validate_document(str(garbage), "pdf")["status"] == "error"

    def test_validation_gate_catches_garbage(self, artifacts_tmp):
        garbage = artifacts_tmp / "garbage.docx"
        garbage.write_bytes(b"not a zip at all")
        assert ds.validate_document(str(garbage), "docx")["status"] == "error"


class TestRegistry:
    async def test_provenance_roundtrip(self, fake_store):
        from services import firestore
        await firestore.create_document_record(
            "f1", "docx_app1_pack_v1.docx", "docx", "Pack", "s-1", "app1",
            "Meridian", "sha256:abc", 1, "app1:pack")
        await firestore.create_document_record(
            "f1", "docx_app1_pack_v2.docx", "docx", "Pack", "s-2", "app1",
            "Meridian", "sha256:def", 2, "app1:pack")
        by_session = await firestore.list_documents("f1", session_id="s-2")
        assert len(by_session) == 1
        assert by_session[0]["version"] == 2
        assert by_session[0]["application_id"] == "app1"
        assert await firestore.next_document_version("f1", "app1:pack") == 3
        # other founder sees nothing
        assert await firestore.list_documents("f2") == []
