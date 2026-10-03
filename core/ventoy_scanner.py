"""
Detects mounted Ventoy drives and lists the ISOs present on them.
Linux and Windows compatible.
"""

import os
import re
import sys
import json
import shutil
import platform
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IsoEntry:
    filename: str
    path: str          # absolute path to the ISO file
    folder: str        # parent folder on the drive (e.g. 'linux', 'windows')
    size_bytes: int
    distro_id: Optional[str] = None    # id found in distros.json
    distro_name: Optional[str] = None
    local_version: Optional[str] = None
    logo_filename: Optional[str] = None


@dataclass
class VentoyDrive:
    mount_point: str
    label: str
    ventoy_version: Optional[str] = None
    iso_entries: list = field(default_factory=list)
    theme_dir: Optional[str] = None          # theme's root folder (e.g. ventoy/M@N/)
    theme_icons_dir: Optional[str] = None    # theme's icons/ subfolder
    ventoy_json_path: Optional[str] = None   # path to ventoy/ventoy.json


def find_ventoy_drives() -> list[VentoyDrive]:
    """Returns the list of detected mounted Ventoy drives."""
    drives = []
    system = platform.system()

    if system == "Linux":
        drives = _find_linux()
    elif system == "Windows":
        drives = _find_windows()

    return drives


def _is_ventoy_mount(path: str) -> bool:
    """
    Checks whether the path is a Ventoy drive: either it has its own
    ventoy/ folder (created once the drive's control files exist — e.g.
    after the first boot into the Ventoy menu, or a theme setup through
    this app), or — for a freshly installed drive that hasn't been booted
    from yet, so its main data partition is still completely empty — it
    sits on a disk that also has a sibling "VTOYEFI" partition, Ventoy's
    own internal boot partition, always written at install time.
    """
    if os.path.isdir(os.path.join(path, "ventoy")):
        return True
    return _has_vtoyefi_sibling(path)


def _has_vtoyefi_sibling(mount_point: str) -> bool:
    """True if the disk this mount point's partition belongs to also has
    a partition labeled "VTOYEFI" (Ventoy's own reserved label — see
    core/ventoy_installer.py, which relies on the same label to recognize
    an existing Ventoy install)."""
    return _find_vtoyefi_partition(mount_point) is not None


def _find_vtoyefi_partition(mount_point: str) -> Optional[str]:
    """Returns the device path (e.g. "/dev/sdb2") of the sibling "VTOYEFI"
    partition on the same disk as `mount_point`, or None if there isn't
    one."""
    try:
        source = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", mount_point],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if not source:
            return None
        parent = subprocess.run(
            ["lsblk", "-n", "-o", "PKNAME", source],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if not parent:
            return None
        result = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,LABEL", f"/dev/{parent}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except Exception:
        return None

    for disk in data.get("blockdevices", []):
        for child in disk.get("children", []) or []:
            if (child.get("label") or "").upper() == "VTOYEFI":
                return f"/dev/{child['name']}"
    return None


def _get_vtoyefi_version(mount_point: str) -> Optional[str]:
    """
    Reads the Ventoy version straight from the sibling VTOYEFI partition's
    grub.cfg (a plain `set VENTOY_VERSION="x.y.z"` line in there) — this is
    the authoritative source Ventoy's own installer reads from (see
    get_disk_ventoy_version() in ventoy_lib.sh), unlike ventoy/ventoy.json
    on the main partition, which only exists once a theme has actually
    been configured.

    Mounts the partition first if it isn't already, then unmounts it right
    back — it's never meant to be presented as a manageable drive.
    """
    part = _find_vtoyefi_partition(mount_point)
    if not part or not shutil.which("udisksctl"):
        return None

    try:
        existing = subprocess.run(
            ["lsblk", "-n", "-o", "MOUNTPOINT", part],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
    except Exception:
        existing = ""

    mounted_by_us = False
    vtoyefi_mount = existing
    if not vtoyefi_mount:
        try:
            result = subprocess.run(
                ["udisksctl", "mount", "-b", part],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return None
            m = re.search(r"at (.+?)\.?\s*$", result.stdout.strip())
            vtoyefi_mount = m.group(1) if m else None
            mounted_by_us = True
        except Exception:
            return None

    if not vtoyefi_mount:
        return None

    version = None
    try:
        with open(os.path.join(vtoyefi_mount, "grub", "grub.cfg"),
                  encoding="utf-8", errors="replace") as f:
            m = re.search(r'VENTOY_VERSION\s*=\s*"([\d.]+)"', f.read())
            if m:
                version = m.group(1)
    except OSError:
        pass

    if mounted_by_us:
        try:
            subprocess.run(["udisksctl", "unmount", "-b", part],
                            capture_output=True, timeout=10)
        except Exception:
            pass

    return version


def _is_vtoyefi_mount(path: str) -> bool:
    """True if `path` is Ventoy's own internal boot partition (always
    labeled "VTOYEFI") — never presented as a manageable drive: it holds
    Ventoy's boot loader payload, not the user's ISOs."""
    return os.path.basename(os.path.normpath(path)).upper() == "VTOYEFI"


def _get_ventoy_version(mount_point: str) -> Optional[str]:
    """Reads the Ventoy version from ventoy/ventoy_release or ventoy.json
    on the main partition, falling back to the sibling VTOYEFI partition's
    grub.cfg — the only place the version is guaranteed to be found on a
    drive that hasn't had its ventoy/ folder created yet (see
    _get_vtoyefi_version)."""
    for candidate in [
        os.path.join(mount_point, "ventoy", "ventoy_release"),
        os.path.join(mount_point, "ventoy", "ventoy.json"),
    ]:
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8", errors="replace") as f:
                    content = f.read()
                m = re.search(r"(\d+\.\d+\.\d+)", content)
                if m:
                    return m.group(1)
            except Exception:
                pass
    return _get_vtoyefi_version(mount_point)


def _find_theme_dir(mount_point: str) -> tuple[Optional[str], Optional[str]]:
    """
    Finds the active theme's root folder and its icons/ subfolder.
    Returns (theme_dir, icons_dir) — either or both can be None.

    Method 1: reads ventoy/ventoy.json -> theme.file -> derives the parent folder.
    Method 2/3: recursive search as a fallback.
    """
    # Method 1: read ventoy.json to find the theme's path
    ventoy_json = os.path.join(mount_point, "ventoy", "ventoy.json")
    if os.path.isfile(ventoy_json):
        try:
            with open(ventoy_json, encoding="utf-8") as f:
                data = json.load(f)
            theme_file = data.get("theme", {}).get("file", "")
            if theme_file:
                # theme_file looks like "/ventoy/M@N/theme.txt"
                theme_file_local = os.path.join(
                    mount_point,
                    theme_file.lstrip("/").replace("/", os.sep)
                )
                theme_dir = os.path.dirname(theme_file_local)
                if os.path.isdir(theme_dir):
                    icons_candidate = os.path.join(theme_dir, "icons")
                    return theme_dir, (icons_candidate if os.path.isdir(icons_candidate) else None)
        except Exception:
            pass

    # Method 2: recursive search in ventoy/themes/
    themes_root = os.path.join(mount_point, "ventoy", "themes")
    if os.path.isdir(themes_root):
        for dirpath, dirnames, _ in os.walk(themes_root):
            if "icons" in dirnames:
                return dirpath, os.path.join(dirpath, "icons")

    # Method 3: searches the whole ventoy/ folder
    ventoy_dir = os.path.join(mount_point, "ventoy")
    if os.path.isdir(ventoy_dir):
        for dirpath, dirnames, _ in os.walk(ventoy_dir):
            if "icons" in dirnames:
                return dirpath, os.path.join(dirpath, "icons")

    return None, None


def _get_drive_label_linux(mount_point: str) -> str:
    """Returns the volume label, or the mount point's name."""
    # Looks it up via /dev/disk/by-label
    label_dir = "/dev/disk/by-label"
    if os.path.isdir(label_dir):
        for label in os.listdir(label_dir):
            link = os.path.realpath(os.path.join(label_dir, label))
            # checks whether this disk matches the mount point
            try:
                result = subprocess.run(
                    ["findmnt", "-n", "-o", "SOURCE", mount_point],
                    capture_output=True, text=True, timeout=3
                )
                if result.returncode == 0:
                    source = result.stdout.strip()
                    if os.path.realpath(os.path.join(label_dir, label)) == os.path.realpath(source):
                        return label.replace("\\x20", " ")
            except Exception:
                pass
    return os.path.basename(mount_point)


def _auto_mount_unmounted_ventoy_linux() -> None:
    """
    Best-effort: mounts the unmounted data partition of any hot-pluggable
    disk that is a Ventoy drive, so it shows up without the user having to
    unplug/replug it or open a file manager — which otherwise would be the
    only way to get it auto-mounted again after this app (or
    Ventoy2Disk.sh itself, mid-install) unmounted it.

    Only disks that already carry a "VTOYEFI" partition are touched: that
    label is readable from lsblk without mounting anything, so any other,
    unrelated USB drive the user deliberately left unmounted is never
    mounted (mounting can replay a journal, flag an NTFS volume as dirty,
    or pop up desktop notifications).
    """
    if not shutil.which("udisksctl"):
        return
    # Lets udev finish processing pending block events (e.g. the partition
    # table a Ventoy install just rewrote) so lsblk reports the new
    # partitions and their labels, not a half-updated view
    if shutil.which("udevadm"):
        try:
            subprocess.run(["udevadm", "settle", "--timeout=5"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,TYPE,HOTPLUG,MOUNTPOINT,FSTYPE,LABEL"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return
        data = json.loads(result.stdout)
    except Exception:
        return

    for disk in data.get("blockdevices", []):
        if disk.get("type") != "disk" or not disk.get("hotplug"):
            continue
        children = disk.get("children", []) or []
        if not any((p.get("label") or "").upper() == "VTOYEFI" for p in children):
            continue
        for part in children:
            if part.get("type") != "part" or part.get("mountpoint") or not part.get("fstype"):
                continue
            # VTOYEFI is Ventoy's own internal boot partition — nothing to
            # detect by mounting it, and it must never be presented as a
            # manageable drive
            if (part.get("label") or "").upper() == "VTOYEFI":
                continue
            dev = f"/dev/{part['name']}"
            try:
                mount_result = subprocess.run(
                    ["udisksctl", "mount", "-b", dev],
                    capture_output=True, text=True, timeout=15,
                )
            except Exception:
                continue
            if mount_result.returncode != 0:
                continue
            # udisksctl prints e.g. "Mounted /dev/sda1 at /run/media/user/Label."
            m = re.search(r"at (.+?)\.?\s*$", mount_result.stdout.strip())
            mountpoint = m.group(1) if m else None
            if not mountpoint or not _is_ventoy_mount(mountpoint):
                try:
                    subprocess.run(["udisksctl", "unmount", "-b", dev],
                                    capture_output=True, timeout=10)
                except Exception:
                    pass


def _find_linux() -> list[VentoyDrive]:
    drives = []
    checked_paths = set()
    search_roots = []

    _auto_mount_unmounted_ventoy_linux()

    # Primary source: /proc/mounts lists exactly the real mount points
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mp = parts[1]
                    if any(mp.startswith(b) for b in ["/media", "/run/media", "/mnt"]):
                        search_roots.append(mp)
    except Exception:
        pass

    # Fallback: scan limited to 2 levels of depth (never rglob)
    # covers /media/LABEL, /media/user/LABEL, /run/media/user/LABEL, /mnt/LABEL
    if not search_roots:
        for base in ["/media", "/run/media", "/mnt"]:
            if not os.path.isdir(base):
                continue
            try:
                base_dev = Path(base).stat().st_dev
                lvl1_entries = list(Path(base).iterdir())
            except OSError:
                continue
            for lvl1 in lvl1_entries:
                try:
                    if not lvl1.is_dir():
                        continue
                    if lvl1.stat().st_dev != base_dev:
                        search_roots.append(str(lvl1))
                        continue
                    for lvl2 in lvl1.iterdir():
                        try:
                            if lvl2.is_dir() and lvl2.stat().st_dev != base_dev:
                                search_roots.append(str(lvl2))
                        except OSError:
                            continue
                except OSError:
                    # e.g. another user's /run/media/<user> (or one created
                    # by a root-elevated process, like a Ventoy install run
                    # via pkexec) that this process can't read into
                    continue

    for mp in search_roots:
        mp = os.path.normpath(mp)
        if mp in checked_paths:
            continue
        checked_paths.add(mp)

        if _is_vtoyefi_mount(mp):
            continue

        if _is_ventoy_mount(mp):
            label = _get_drive_label_linux(mp)
            version = _get_ventoy_version(mp)
            theme_dir, icons_dir = _find_theme_dir(mp)
            vjson = os.path.join(mp, "ventoy", "ventoy.json")
            drives.append(VentoyDrive(
                mount_point=mp,
                label=label,
                ventoy_version=version,
                theme_dir=theme_dir,
                theme_icons_dir=icons_dir,
                ventoy_json_path=vjson if os.path.isfile(vjson) else None,
            ))

    return drives


def _find_windows() -> list[VentoyDrive]:
    drives = []
    import string
    for letter in string.ascii_uppercase:
        mp = f"{letter}:\\"
        if os.path.isdir(mp) and _is_ventoy_mount(mp):
            try:
                import ctypes
                vol_label = ctypes.create_unicode_buffer(261)
                ctypes.windll.kernel32.GetVolumeInformationW(
                    mp, vol_label, 261, None, None, None, None, 0
                )
                label = vol_label.value or letter
            except Exception:
                label = letter
            version = _get_ventoy_version(mp)
            theme_dir, icons_dir = _find_theme_dir(mp)
            vjson = os.path.join(mp, "ventoy", "ventoy.json")
            drives.append(VentoyDrive(
                mount_point=mp,
                label=label,
                ventoy_version=version,
                theme_dir=theme_dir,
                theme_icons_dir=icons_dir,
                ventoy_json_path=vjson if os.path.isfile(vjson) else None,
            ))
    return drives


def scan_isos(drive: VentoyDrive, distros_db: dict) -> list[IsoEntry]:
    """
    Scans the Ventoy drive and returns the list of ISOs found.
    Matches each ISO to a known distro when possible.
    """
    entries = []
    mount = drive.mount_point

    for dirpath, dirnames, filenames in os.walk(mount):
        # Ignores the ventoy/ folder (internal config)
        dirnames[:] = [
            d for d in dirnames
            if not (d.lower() == "ventoy" and dirpath == mount)
        ]

        for fname in filenames:
            if not fname.lower().endswith(".iso"):
                continue

            full_path = os.path.join(dirpath, fname)

            # Ignores symlinks (security: avoids following malicious links)
            if os.path.islink(full_path):
                continue
            rel_folder = os.path.relpath(dirpath, mount)
            if rel_folder == ".":
                rel_folder = "/"

            try:
                size = os.path.getsize(full_path)
            except OSError:
                size = 0

            entry = IsoEntry(
                filename=fname,
                path=full_path,
                folder=rel_folder,
                size_bytes=size,
            )

            _match_distro(entry, distros_db)
            entries.append(entry)

    # Sort: by folder then by name
    entries.sort(key=lambda e: (e.folder, e.filename))
    return entries


def _match_distro(entry: IsoEntry, distros_db: dict) -> None:
    """Attempts to match an IsoEntry to a known distro."""
    for distro in distros_db.get("distros", []):
        for pattern in distro.get("filename_patterns", []):
            if re.search(pattern, entry.filename, re.IGNORECASE):
                entry.distro_id = distro["id"]
                entry.distro_name = distro["name"]
                entry.logo_filename = distro.get("logo_filename")

                version_regex = distro.get("version_regex")
                if version_regex:
                    m = re.search(version_regex, entry.filename, re.IGNORECASE)
                    if m:
                        entry.local_version = m.group(1)
                return


def format_size(size_bytes: int) -> str:
    """Formats a size in bytes into a readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def load_distros_db(json_path: Optional[str] = None) -> dict:
    """Loads the distro database from distros.json."""
    if json_path is None:
        base = Path(__file__).parent.parent
        json_path = base / "data" / "distros.json"
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "distros" not in data:
            raise ValueError("Format distros.json invalide : clé 'distros' manquante")
        return data
    except (FileNotFoundError, PermissionError) as e:
        raise FileNotFoundError(f"Impossible d'ouvrir {json_path} : {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"distros.json corrompu : {e}") from e
