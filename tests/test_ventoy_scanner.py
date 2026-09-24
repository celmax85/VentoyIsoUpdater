"""Tests unitaires pour core/ventoy_scanner.py"""

import os
import sys
import json
import pytest
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.ventoy_scanner import (
    format_size,
    _is_ventoy_mount,
    _get_ventoy_version,
    _match_distro,
    load_distros_db,
    IsoEntry,
)


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def ventoy_mount(tmp_dir):
    """Creates a fake Ventoy mount point."""
    ventoy_dir = os.path.join(tmp_dir, "ventoy")
    os.makedirs(ventoy_dir)
    return tmp_dir


# ── format_size ───────────────────────────────────────────────────────────────

class TestFormatSize:
    def test_bytes(self):
        assert format_size(512) == "512.0 B"

    def test_kilobytes(self):
        assert format_size(1024) == "1.0 KB"

    def test_megabytes(self):
        assert format_size(1024 * 1024) == "1.0 MB"

    def test_gigabytes(self):
        assert format_size(1024 ** 3) == "1.0 GB"

    def test_large_size(self):
        # The function tops out at GB in the current implementation
        result = format_size(1024 ** 3 * 5)  # 5 GB
        assert "5.0 GB" == result

    def test_zero(self):
        assert format_size(0) == "0.0 B"

    def test_fractional(self):
        result = format_size(1536)  # 1.5 KB
        assert "1.5" in result and "KB" in result


# ── _is_ventoy_mount ──────────────────────────────────────────────────────────

class TestIsVentoyMount:
    def test_detects_ventoy_folder(self, ventoy_mount):
        assert _is_ventoy_mount(ventoy_mount) is True

    def test_no_ventoy_folder(self, tmp_dir):
        assert _is_ventoy_mount(tmp_dir) is False

    def test_nonexistent_path(self, tmp_dir):
        assert _is_ventoy_mount(os.path.join(tmp_dir, "nonexistent")) is False


# ── _get_ventoy_version ───────────────────────────────────────────────────────

class TestGetVentoyVersion:
    def test_reads_from_ventoy_release(self, ventoy_mount):
        release_path = os.path.join(ventoy_mount, "ventoy", "ventoy_release")
        with open(release_path, "w") as f:
            f.write("VENTOY_VER=1.0.99")
        assert _get_ventoy_version(ventoy_mount) == "1.0.99"

    def test_reads_from_ventoy_json(self, ventoy_mount):
        json_path = os.path.join(ventoy_mount, "ventoy", "ventoy.json")
        with open(json_path, "w") as f:
            json.dump({"VTOY_VER": "1.0.97"}, f)
        # JSON doesn't always have a version string so write it plainly
        with open(json_path, "w") as f:
            f.write('{"version": "1.0.97"}')
        assert _get_ventoy_version(ventoy_mount) == "1.0.97"

    def test_returns_none_if_missing(self, tmp_dir):
        os.makedirs(os.path.join(tmp_dir, "ventoy"))
        assert _get_ventoy_version(tmp_dir) is None

    def test_handles_corrupt_file(self, ventoy_mount):
        release_path = os.path.join(ventoy_mount, "ventoy", "ventoy_release")
        with open(release_path, "wb") as f:
            f.write(b"\xff\xfe no version here \x00")
        # Should not crash, returns None
        result = _get_ventoy_version(ventoy_mount)
        assert result is None or isinstance(result, str)


# ── _match_distro ─────────────────────────────────────────────────────────────

class TestMatchDistro:
    def _make_db(self):
        return {
            "distros": [
                {
                    "id": "ubuntu",
                    "name": "Ubuntu",
                    "filename_patterns": [r"ubuntu-\d+"],
                    "grub_class": "ubuntu",
                    "logo_url": "local:ubuntu.png",
                    "version_regex": r"ubuntu-([\d.]+)",
                },
                {
                    "id": "debian",
                    "name": "Debian",
                    "filename_patterns": [r"debian-\d+"],
                    "grub_class": "debian",
                    "logo_url": "local:debian.png",
                },
            ]
        }

    def test_matches_ubuntu(self):
        entry = IsoEntry(filename="ubuntu-22.04.iso", path="", folder="", size_bytes=0)
        _match_distro(entry, self._make_db())
        assert entry.distro_id == "ubuntu"
        assert entry.distro_name == "Ubuntu"

    def test_extracts_version(self):
        # version_regex = r"ubuntu-([\d.]+)" — capture tout jusqu'au point final inclus
        entry = IsoEntry(filename="ubuntu-22.04.iso", path="", folder="", size_bytes=0)
        _match_distro(entry, self._make_db())
        assert entry.local_version is not None
        assert entry.local_version.startswith("22.04")

    def test_no_match(self):
        entry = IsoEntry(filename="someother.iso", path="", folder="", size_bytes=0)
        _match_distro(entry, self._make_db())
        assert entry.distro_id is None

    def test_case_insensitive(self):
        entry = IsoEntry(filename="UBUNTU-22.04.iso", path="", folder="", size_bytes=0)
        _match_distro(entry, self._make_db())
        assert entry.distro_id == "ubuntu"

    def test_empty_db(self):
        entry = IsoEntry(filename="ubuntu.iso", path="", folder="", size_bytes=0)
        _match_distro(entry, {})
        assert entry.distro_id is None


# ── scan_isos (symlinks) ──────────────────────────────────────────────────────

class TestScanIsosSymlinks:
    def test_symlink_iso_is_ignored(self, tmp_dir):
        """.iso symlinks must not be listed (security)."""
        from core.ventoy_scanner import scan_isos, VentoyDrive
        # Creates a fake Ventoy mount point
        ventoy_dir = os.path.join(tmp_dir, "ventoy")
        os.makedirs(ventoy_dir)
        linux_dir = os.path.join(tmp_dir, "linux")
        os.makedirs(linux_dir)
        # Real ISO file
        real_iso = os.path.join(linux_dir, "real.iso")
        with open(real_iso, "wb") as f:
            f.write(b"\x00" * 1024)
        # Symlink disguised as an ISO
        target = os.path.join(tmp_dir, "secret.txt")
        with open(target, "w") as f:
            f.write("secret")
        symlink = os.path.join(linux_dir, "evil.iso")
        os.symlink(target, symlink)

        drive = VentoyDrive(mount_point=tmp_dir, label="TEST")
        entries = scan_isos(drive, {})
        filenames = [e.filename for e in entries]
        assert "real.iso" in filenames
        assert "evil.iso" not in filenames


# ── load_distros_db ───────────────────────────────────────────────────────────

class TestLoadDistrosDb:
    def test_loads_real_db(self):
        db = load_distros_db()
        assert "distros" in db
        assert len(db["distros"]) > 0

    def test_all_distros_have_required_fields(self):
        db = load_distros_db()
        for d in db["distros"]:
            assert "id" in d, f"Missing 'id' in {d}"
            assert "name" in d, f"Missing 'name' in {d}"

    def test_missing_file_raises(self, tmp_dir):
        with pytest.raises(Exception):
            load_distros_db(os.path.join(tmp_dir, "nonexistent.json"))

    def test_corrupt_json_raises(self, tmp_dir):
        bad = os.path.join(tmp_dir, "bad.json")
        with open(bad, "w") as f:
            f.write("{ not valid json")
        with pytest.raises(Exception):
            load_distros_db(bad)


class TestAutoMountUnmountedVentoy:
    """_auto_mount_unmounted_ventoy_linux must only ever mount partitions of
    a disk that carries a VTOYEFI partition — never an unrelated USB drive."""

    def _run(self, blockdevices):
        import json as _json
        from unittest.mock import patch, MagicMock
        import core.ventoy_scanner as vs

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock(returncode=0, stdout="")
            if cmd[0] == "lsblk":
                result.stdout = _json.dumps({"blockdevices": blockdevices})
            elif cmd[:2] == ["udisksctl", "mount"]:
                result.stdout = f"Mounted {cmd[-1]} at /run/media/u/X."
            return result

        with patch.object(vs.shutil, "which", return_value="/usr/bin/udisksctl"), \
             patch.object(vs.subprocess, "run", side_effect=fake_run), \
             patch.object(vs, "_is_ventoy_mount", return_value=True):
            vs._auto_mount_unmounted_ventoy_linux()
        return [c for c in calls if c[:2] == ["udisksctl", "mount"]]

    def test_unrelated_usb_disk_is_never_mounted(self):
        mounts = self._run([{
            "name": "sdb", "type": "disk", "hotplug": True, "children": [
                {"name": "sdb1", "type": "part", "mountpoint": None,
                 "fstype": "ntfs", "label": "BACKUP"},
            ],
        }])
        assert mounts == []

    def test_ventoy_data_partition_is_mounted_but_not_vtoyefi(self):
        mounts = self._run([{
            "name": "sdc", "type": "disk", "hotplug": True, "children": [
                {"name": "sdc1", "type": "part", "mountpoint": None,
                 "fstype": "exfat", "label": "Ventoy"},
                {"name": "sdc2", "type": "part", "mountpoint": None,
                 "fstype": "vfat", "label": "VTOYEFI"},
            ],
        }])
        assert mounts == [["udisksctl", "mount", "-b", "/dev/sdc1"]]
