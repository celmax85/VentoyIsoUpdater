"""
Operations on ISO files: download, deletion.
ISOs are never automatically replaced — several versions can coexist.
"""

import os
import re
import shutil
from typing import Optional


def get_download_path(dest_folder: str, filename: str) -> str:
    """
    Returns the destination path for a download into dest_folder.
    Creates the folder if needed.
    If a file with the same name already exists, appends a numeric suffix.

    ``filename`` comes from VersionInfo, whose value is extracted by the
    sources/ checkers from remote pages/APIs (regex over HTML or JSON) — so
    it isn't trusted blindly. ``os.path.basename`` neutralizes any path
    traversal attempt (``../../etc/...`` or an absolute path) before it's
    joined with dest_folder.
    """
    filename = os.path.basename(filename)
    if not filename or filename in (".", ".."):
        raise ValueError(f"Invalid filename: {filename!r}")
    dest_folder = os.path.realpath(dest_folder)
    os.makedirs(dest_folder, exist_ok=True)
    path = os.path.join(dest_folder, filename)
    if not os.path.exists(path):
        return path
    # File already present -> suffix to avoid overwriting
    base, ext = os.path.splitext(filename)
    # Compound extensions (e.g. Memtest86+'s "*.iso.zip", auto-unwrapped to
    # "*.iso" after download — see core.downloader) need the suffix before
    # the *real* extension, not spliced into the middle of it: without this,
    # splitext's single-dot split turns "name.iso.zip" into "name.iso_2.zip"
    # instead of the intended "name_2.iso.zip".
    if ext.lower() == ".zip":
        inner_base, inner_ext = os.path.splitext(base)
        if inner_ext.lower() in (".iso", ".img"):
            base, ext = inner_base, inner_ext + ext
    MAX_SUFFIX = 999
    for i in range(1, MAX_SUFFIX + 1):
        candidate = os.path.join(dest_folder, f"{base}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
    raise OSError(f"Could not find a free name for {filename} in {dest_folder}")


# Category -> candidate folder names on the drive. The first one is the
# folder created when none of them exists yet; the others are accepted
# spellings of an existing folder, reused as-is.
_CATEGORY_FOLDERS = {
    "linux":    ["linux", "Linux"],
    "gaming":   ["gaming", "Gaming"],
    "server":   ["server", "Server"],
    "security": ["security", "Security"],
    "bsd":      ["bsd", "BSD"],
    "windows":  ["windows", "Windows"],
    # Boot/repair utilities (Memtest86+, GParted, Clonezilla...)
    "tools":    ["tools", "Tools"],
}

# Characters exFAT/FAT32 (Ventoy's data partition) and Windows refuse in a
# file name; "/" would also silently create a nested folder
_UNSAFE_FOLDER_CHARS = re.compile(r'[\\/:*?"<>|]')


def _safe_folder_name(name: str) -> str:
    """Turns a distro display name into a single, portable folder name
    (e.g. "Pop!_OS (Intel/AMD)" -> "Pop!_OS (Intel-AMD)")."""
    cleaned = _UNSAFE_FOLDER_CHARS.sub("-", name).strip(" .")
    return cleaned or "ISO"


def suggest_dest_folder(mount_point: str, distro_cfg: dict, iso_entries: list) -> str:
    """
    Returns the suggested destination folder for downloading a new ISO,
    so the drive stays organized as <category>/<distro>/.

    Priority:
    1. A folder already used by ISOs of this distro on the drive
    2. The category folder (an existing one in any accepted spelling, or
       the default one, created on download) + a subfolder named after
       the distro
    """
    distro_id = distro_cfg.get("id", "")
    distro_name = _safe_folder_name(distro_cfg.get("name", distro_id))
    category = distro_cfg.get("category", "linux")

    # 1. Look for an existing folder for this distro among the ISOs already present
    for entry in iso_entries:
        if entry.distro_id == distro_id and entry.folder and entry.folder != "/":
            candidate = os.path.join(mount_point, entry.folder.lstrip("/"))
            if os.path.isdir(candidate):
                return candidate

    # 2. Category folder: reuse an existing one, otherwise the default name
    # (the download step creates it)
    candidates = _CATEGORY_FOLDERS.get(category, [_safe_folder_name(category).lower()])
    cat_folder = next(
        (name for name in candidates if os.path.isdir(os.path.join(mount_point, name))),
        candidates[0],
    )
    return os.path.join(mount_point, cat_folder, distro_name)


def delete_iso(path: str) -> bool:
    """Deletes an ISO. Returns True on success."""
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def get_free_space(path: str) -> int:
    """Returns the free disk space in bytes for the given path."""
    return shutil.disk_usage(path).free


def list_isos_in_folder(folder: str) -> list[str]:
    """Lists every ISO in a folder (non-recursive)."""
    try:
        return [
            f for f in os.listdir(folder)
            if f.lower().endswith(".iso")
        ]
    except OSError:
        return []
