"""Unit tests for core/ventoy_installer.py (cached Ventoy re-verification)"""

import hashlib
import io
import os
import sys
import shutil
import tarfile
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.ventoy_installer as vi


VERSION = "1.1.05"


@pytest.fixture
def cache_dir():
    d = tempfile.mkdtemp()
    with patch.object(vi, "_VENTOY_CACHE_DIR", d), \
         patch.object(vi.platform, "system", return_value="Linux"):
        yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_cached_release(cache_dir: str, script_body: bytes = b"#!/bin/sh\necho ok\n") -> str:
    """Builds a cached release the way download_ventoy() leaves it: the
    archive plus its extracted ventoy-<version>/ folder. Returns the
    archive's SHA256."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(f"ventoy-{VERSION}/Ventoy2Disk.sh")
        info.size = len(script_body)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(script_body))
    data = buf.getvalue()
    archive = os.path.join(cache_dir, f"ventoy-{VERSION}-linux.tar.gz")
    with open(archive, "wb") as f:
        f.write(data)
    extracted = os.path.join(cache_dir, f"ventoy-{VERSION}")
    os.makedirs(extracted)
    with open(os.path.join(extracted, "Ventoy2Disk.sh"), "wb") as f:
        f.write(script_body)
    return hashlib.sha256(data).hexdigest()


class TestFindCachedVentoy:
    def test_found_when_archive_is_cached(self, cache_dir):
        _make_cached_release(cache_dir)
        script = vi.find_cached_ventoy()
        assert script == os.path.join(cache_dir, f"ventoy-{VERSION}", "Ventoy2Disk.sh")

    def test_ignored_when_archive_is_missing(self, cache_dir):
        """A cache left by a build that deleted the archive can't be
        re-verified, so it must not be offered for install."""
        _make_cached_release(cache_dir)
        os.remove(os.path.join(cache_dir, f"ventoy-{VERSION}-linux.tar.gz"))
        assert vi.find_cached_ventoy() is None


class TestPrepareVerifiedVentoy:
    def test_system_install_is_returned_unchanged(self, cache_dir):
        assert vi.prepare_verified_ventoy("/usr/share/ventoy/Ventoy2Disk.sh") == \
            ("/usr/share/ventoy/Ventoy2Disk.sh", None)

    def test_cached_copy_runs_from_fresh_extraction(self, cache_dir):
        sha = _make_cached_release(cache_dir)
        cached_script = vi.find_cached_ventoy()
        # Tampering with the extracted copy must have no effect: only the
        # verified archive is used
        with open(cached_script, "wb") as f:
            f.write(b"#!/bin/sh\nrm -rf /\n")
        with patch.object(vi, "_fetch_ventoy_checksum", return_value=sha):
            script, tmp_dir = vi.prepare_verified_ventoy(cached_script)
        try:
            assert not script.startswith(cache_dir)
            assert script.startswith(tmp_dir)
            with open(script, "rb") as f:
                assert f.read() == b"#!/bin/sh\necho ok\n"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_tampered_archive_is_rejected(self, cache_dir):
        _make_cached_release(cache_dir)
        with patch.object(vi, "_fetch_ventoy_checksum", return_value="0" * 64):
            assert vi.prepare_verified_ventoy(vi.find_cached_ventoy()) is None

    def test_offline_is_rejected(self, cache_dir):
        _make_cached_release(cache_dir)
        with patch.object(vi, "_fetch_ventoy_checksum", return_value=None):
            assert vi.prepare_verified_ventoy(vi.find_cached_ventoy()) is None
