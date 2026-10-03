"""Tests unitaires pour core/iso_manager.py"""

import os
import sys
import pytest
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.iso_manager import (
    get_download_path,
    suggest_dest_folder,
    get_free_space,
    list_isos_in_folder,
    delete_iso,
)


class FakeIsoEntry:
    def __init__(self, distro_id, folder, path=""):
        self.distro_id = distro_id
        self.folder = folder
        self.path = path


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ── get_download_path ─────────────────────────────────────────────────────────

class TestGetDownloadPath:
    def test_new_file(self, tmp_dir):
        path = get_download_path(tmp_dir, "ubuntu.iso")
        assert path == os.path.join(tmp_dir, "ubuntu.iso")

    def test_creates_directory(self, tmp_dir):
        subdir = os.path.join(tmp_dir, "new_folder")
        path = get_download_path(subdir, "test.iso")
        assert os.path.isdir(subdir)
        assert path == os.path.join(subdir, "test.iso")

    def test_suffix_on_existing_file(self, tmp_dir):
        existing = os.path.join(tmp_dir, "ubuntu.iso")
        open(existing, "w").close()
        path = get_download_path(tmp_dir, "ubuntu.iso")
        assert path == os.path.join(tmp_dir, "ubuntu_1.iso")

    def test_suffix_increments(self, tmp_dir):
        for name in ["ubuntu.iso", "ubuntu_1.iso", "ubuntu_2.iso"]:
            open(os.path.join(tmp_dir, name), "w").close()
        path = get_download_path(tmp_dir, "ubuntu.iso")
        assert path == os.path.join(tmp_dir, "ubuntu_3.iso")

    def test_raises_after_999(self, tmp_dir):
        open(os.path.join(tmp_dir, "f.iso"), "w").close()
        for i in range(1, 1000):
            open(os.path.join(tmp_dir, f"f_{i}.iso"), "w").close()
        with pytest.raises(OSError):
            get_download_path(tmp_dir, "f.iso")

    def test_suffix_on_compound_iso_zip_extension(self, tmp_dir):
        """Memtest86+'s '*.iso.zip' shape: the disambiguation suffix must
        land before the real ".iso.zip" extension, not spliced into the
        middle of it ("name.iso_1.zip")."""
        existing = os.path.join(tmp_dir, "mt86plus_8.10_x86_64.iso.zip")
        open(existing, "w").close()
        path = get_download_path(tmp_dir, "mt86plus_8.10_x86_64.iso.zip")
        assert path == os.path.join(tmp_dir, "mt86plus_8.10_x86_64_1.iso.zip")

    def test_no_extension(self, tmp_dir):
        path = get_download_path(tmp_dir, "noext")
        assert path == os.path.join(tmp_dir, "noext")


# ── suggest_dest_folder ───────────────────────────────────────────────────────

class TestSuggestDestFolder:
    def test_uses_existing_iso_folder(self, tmp_dir):
        linux_dir = os.path.join(tmp_dir, "linux")
        os.makedirs(linux_dir)
        entry = FakeIsoEntry("ubuntu", "linux")
        result = suggest_dest_folder(tmp_dir, {"id": "ubuntu", "category": "linux"}, [entry])
        assert result == linux_dir

    def test_uses_category_folder(self, tmp_dir):
        linux_dir = os.path.join(tmp_dir, "linux")
        os.makedirs(linux_dir)
        result = suggest_dest_folder(tmp_dir, {"id": "ubuntu", "name": "Ubuntu", "category": "linux"}, [])
        assert result == os.path.join(linux_dir, "Ubuntu")

    def test_tools_never_land_in_linux_folder(self, tmp_dir):
        """The "tools" category (Memtest86+, GParted...) has its own folder
        and must not fall back into linux/ like an unknown category would."""
        os.makedirs(os.path.join(tmp_dir, "linux"))
        cfg = {"id": "memtest86plus", "name": "Memtest86+", "category": "tools"}
        assert suggest_dest_folder(tmp_dir, cfg, []) == os.path.join(tmp_dir, "Memtest86+")
        os.makedirs(os.path.join(tmp_dir, "tools"))
        assert suggest_dest_folder(tmp_dir, cfg, []) == os.path.join(tmp_dir, "tools", "Memtest86+")

    def test_fallback_to_root(self, tmp_dir):
        result = suggest_dest_folder(tmp_dir, {"id": "ubuntu", "name": "Ubuntu", "category": "linux"}, [])
        # No linux folder exists → fallback to root/Ubuntu
        assert result == os.path.join(tmp_dir, "Ubuntu")

    def test_ignores_root_iso_folder(self, tmp_dir):
        """ISO at root / should not be used as dest folder."""
        entry = FakeIsoEntry("ubuntu", "/")
        result = suggest_dest_folder(tmp_dir, {"id": "ubuntu", "name": "Ubuntu", "category": "linux"}, [entry])
        assert result == os.path.join(tmp_dir, "Ubuntu")

    def test_windows_category(self, tmp_dir):
        win_dir = os.path.join(tmp_dir, "windows")
        os.makedirs(win_dir)
        result = suggest_dest_folder(tmp_dir, {"id": "win11", "name": "Windows 11", "category": "windows"}, [])
        assert result == os.path.join(win_dir, "Windows 11")


# ── get_free_space ────────────────────────────────────────────────────────────

class TestGetFreeSpace:
    def test_returns_int(self, tmp_dir):
        space = get_free_space(tmp_dir)
        assert isinstance(space, int)
        assert space > 0


# ── list_isos_in_folder ───────────────────────────────────────────────────────

class TestListIsosInFolder:
    def test_finds_iso_files(self, tmp_dir):
        for name in ["a.iso", "b.ISO", "c.txt", "d.iso"]:
            open(os.path.join(tmp_dir, name), "w").close()
        result = list_isos_in_folder(tmp_dir)
        assert set(result) == {"a.iso", "b.ISO", "d.iso"}

    def test_empty_folder(self, tmp_dir):
        assert list_isos_in_folder(tmp_dir) == []

    def test_nonexistent_folder(self):
        assert list_isos_in_folder("/nonexistent/path/abc") == []


# ── delete_iso ────────────────────────────────────────────────────────────────

class TestDeleteIso:
    def test_delete_existing(self, tmp_dir):
        path = os.path.join(tmp_dir, "test.iso")
        open(path, "w").close()
        assert delete_iso(path) is True
        assert not os.path.exists(path)

    def test_delete_nonexistent(self, tmp_dir):
        assert delete_iso(os.path.join(tmp_dir, "ghost.iso")) is False
