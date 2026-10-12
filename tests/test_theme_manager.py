"""Tests unitaires pour core/theme_manager.py"""

import os
import sys
import json
import pytest
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.theme_manager import (
    load_ventoy_json,
    save_ventoy_json,
    get_existing_menu_classes,
    add_menu_class_entry,
    get_missing_logos,
)
from core.ventoy_scanner import IsoEntry


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def ventoy_json(tmp_dir):
    path = os.path.join(tmp_dir, "ventoy.json")
    data = {
        "menu_class": [
            {"parent": "/linux/ubuntu", "class": "ubuntu"},
            {"parent": "/Linux", "class": "linux"},
            {"dir": "/Tools", "class": "tools"},  # folder entry only, ignored for ISOs
        ]
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return path


# ── load_ventoy_json ──────────────────────────────────────────────────────────

class TestLoadVentoyJson:
    def test_loads_valid(self, ventoy_json):
        data = load_ventoy_json(ventoy_json)
        assert "menu_class" in data
        assert len(data["menu_class"]) == 3

    def test_missing_file_returns_empty(self, tmp_dir):
        result = load_ventoy_json(os.path.join(tmp_dir, "nonexistent.json"))
        assert result == {}

    def test_corrupt_json_returns_empty(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            f.write("{ bad json !!")
        assert load_ventoy_json(path) == {}


# ── save_ventoy_json ──────────────────────────────────────────────────────────

class TestSaveVentoyJson:
    def test_saves_and_reloads(self, tmp_dir):
        path = os.path.join(tmp_dir, "ventoy.json")
        data = {"menu_class": [{"dir": "/test", "class": "test"}]}
        assert save_ventoy_json(path, data) is True
        reloaded = load_ventoy_json(path)
        assert reloaded == data

    def test_creates_backup(self, ventoy_json):
        backup = ventoy_json + ".bak"
        save_ventoy_json(ventoy_json, {"menu_class": []})
        assert os.path.isfile(backup)

    def test_restores_backup_on_write_failure(self, ventoy_json):
        """On a write failure, save returns False and the backup exists."""
        from unittest.mock import patch
        original = load_ventoy_json(ventoy_json)
        with patch("json.dump", side_effect=IOError("disk full")):
            result = save_ventoy_json(ventoy_json, {"menu_class": []})
        assert result is False
        # The backup must exist (created before the write attempt)
        assert os.path.isfile(ventoy_json + ".bak")
        # The original content must be restored from the backup
        restored = load_ventoy_json(ventoy_json)
        assert restored == original

    def test_save_without_existing_file_creates_it(self, tmp_dir):
        """Sauvegarde dans un fichier qui n'existait pas encore."""
        path = os.path.join(tmp_dir, "new_ventoy.json")
        data = {"theme": {"file": "/ventoy/theme/theme.txt"}}
        assert save_ventoy_json(path, data) is True
        assert load_ventoy_json(path) == data


# ── get_existing_menu_classes ─────────────────────────────────────────────────

class TestGetExistingMenuClasses:
    def test_returns_dict(self, ventoy_json):
        result = get_existing_menu_classes(ventoy_json)
        assert isinstance(result, dict)
        assert result.get("/linux/ubuntu") == "ubuntu"
        assert result.get("/Linux") == "linux"
        assert "/Tools" not in result

    def test_empty_if_no_menu_class(self, tmp_dir):
        path = os.path.join(tmp_dir, "ventoy.json")
        with open(path, "w") as f:
            json.dump({}, f)
        assert get_existing_menu_classes(path) == {}


# ── add_menu_class_entry ──────────────────────────────────────────────────────

class TestAddMenuClassEntry:
    def test_adds_new_entry(self, ventoy_json):
        result = add_menu_class_entry(ventoy_json, "linux/debian", "debian")
        assert result is True
        data = load_ventoy_json(ventoy_json)
        assert {"parent": "/linux/debian", "class": "debian"} in data["menu_class"]

    def test_does_not_duplicate(self, ventoy_json):
        add_menu_class_entry(ventoy_json, "linux/ubuntu", "ubuntu")
        result = add_menu_class_entry(ventoy_json, "linux/ubuntu", "ubuntu")
        assert result is False

    def test_normalizes_windows_and_root_paths(self, ventoy_json):
        add_menu_class_entry(ventoy_json, "Linux\\Fedora", "fedora")
        add_menu_class_entry(ventoy_json, ".", "kali")  # relpath of the drive root
        parents = [e.get("parent") for e in load_ventoy_json(ventoy_json)["menu_class"]]
        assert "/Linux/Fedora" in parents
        assert "/" in parents

    def test_unmatched_uses_ventoy_rules(self, ventoy_json):
        from core.theme_manager import get_unmatched_isos
        iso = lambda folder, name="x.iso": IsoEntry(filename=name, path="", folder=folder, size_bytes=0)
        save_ventoy_json(ventoy_json, {"menu_class": [
            {"parent": "/Linux", "class": "linux"},
            {"key": "debian", "class": "debian"},
            {"dir": "/Tools", "class": "tools"},
        ]})
        entries = [iso("Linux"), iso("Linux/Arch"), iso("Tools"), iso("/", "debian-13.iso")]
        unmatched = get_unmatched_isos(entries, ventoy_json)
        # parent doesn't cover subfolders, dir never covers ISOs
        assert [e.folder for e in unmatched] == ["Linux/Arch", "Tools"]

    def test_nonexistent_file_returns_false(self, tmp_dir):
        result = add_menu_class_entry(
            os.path.join(tmp_dir, "missing.json"), "linux", "ubuntu"
        )
        assert result is False


# ── get_missing_logos ─────────────────────────────────────────────────────────

class TestGetMissingLogos:
    def _make_iso(self, distro_id):
        return IsoEntry(filename="test.iso", path="", folder="linux", size_bytes=0,
                        distro_id=distro_id)

    def _make_db(self):
        return {
            "distros": [
                {"id": "ubuntu", "name": "Ubuntu", "grub_class": "ubuntu",
                 "filename_patterns": [], "logo_url": "local:ubuntu.png"},
                {"id": "debian", "name": "Debian", "grub_class": "debian",
                 "filename_patterns": [], "logo_url": "local:debian.png"},
            ]
        }

    def test_detects_missing(self, tmp_dir):
        open(os.path.join(tmp_dir, "ubuntu.png"), "w").close()
        entries = [self._make_iso("ubuntu"), self._make_iso("debian")]
        missing = get_missing_logos(tmp_dir, entries, self._make_db())
        assert "debian.png" in missing
        assert "ubuntu.png" not in missing

    def test_empty_when_all_present(self, tmp_dir):
        for name in ["ubuntu.png", "debian.png"]:
            open(os.path.join(tmp_dir, name), "w").close()
        entries = [self._make_iso("ubuntu"), self._make_iso("debian")]
        assert get_missing_logos(tmp_dir, entries, self._make_db()) == []

    def test_nonexistent_dir_returns_empty(self, tmp_dir):
        result = get_missing_logos(
            os.path.join(tmp_dir, "nope"), [self._make_iso("ubuntu")], self._make_db()
        )
        assert result == []
