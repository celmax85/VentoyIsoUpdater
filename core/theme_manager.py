"""
Manages distro logos for the existing Ventoy theme.

Logic:
- Logos are named after the distro's `grub_class` (= GRUB class name)
  e.g.: grub_class="Zorin" -> file "Zorin.png" in icons/
- ventoy.json contains `menu_class` entries that map folders -> GRUB classes
- When a new logo is added, adding the matching menu_class entry to
  ventoy.json is also offered
"""

import os
import json
import shutil
from typing import Callable, Optional

from core.downloader import download_logo


# ── Reading / writing ventoy.json ────────────────────────────────────────────

def load_ventoy_json(ventoy_json_path: str) -> dict:
    """Loads ventoy.json. Returns {} if absent or unreadable."""
    try:
        with open(ventoy_json_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_ventoy_json(ventoy_json_path: str, data: dict) -> bool:
    """Saves ventoy.json with clean indentation. Returns True on success."""
    backup = ventoy_json_path + ".bak"
    backup_created = False
    if os.path.isfile(ventoy_json_path):
        try:
            shutil.copy2(ventoy_json_path, backup)
            backup_created = True
        except Exception:
            pass  # No backup available, continues anyway
    try:
        with open(ventoy_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception:
        # Restores the backup if the write fails
        if backup_created:
            try:
                shutil.copy2(backup, ventoy_json_path)
            except Exception:
                pass  # Cannot restore: ventoy.json may be corrupted
        return False


def _find_ventoy_sample_theme() -> Optional[str]:
    """
    Locates Ventoy's own bundled sample theme (shipped as
    plugin/ventoy/theme/ in the official release archive — a complete,
    ready-to-use theme with its background and menu pixmaps, not just a
    bare theme.txt) inside the persistent Ventoy download cache,
    downloading the latest release there first if nothing is cached yet.

    Returns the theme folder's path, or None if it can't be found or
    downloaded (offline with nothing cached, for instance) — the caller
    falls back to a bare theme.txt in that case.
    """
    from core.ventoy_installer import (
        get_ventoy_cache_dir, find_cached_ventoy, download_ventoy, get_ventoy_latest_version,
    )

    def _sample_in(ventoy_release_dir: str) -> Optional[str]:
        candidate = os.path.join(ventoy_release_dir, "plugin", "ventoy", "theme")
        return candidate if os.path.isfile(os.path.join(candidate, "theme.txt")) else None

    cached_script = find_cached_ventoy()
    if cached_script:
        sample = _sample_in(os.path.dirname(cached_script))
        if sample:
            return sample

    version = get_ventoy_latest_version()
    if not version:
        return None
    extracted = download_ventoy(version=version, dest_dir=get_ventoy_cache_dir())
    return _sample_in(extracted) if extracted else None


def create_default_theme(mount_point: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Bootstraps the ventoy/ structure needed for theme and logo management
    on a drive that has none yet — the normal state right after
    installing Ventoy: nothing creates ventoy/ventoy.json until a theme
    is actually configured (Ventoy's own grub.cfg only reads it from the
    main partition if it's there; it never creates it itself).

    Creates ventoy/ventoy.json (pointing "theme.file" at the theme.txt
    below unless it already points somewhere else) and ventoy/theme/,
    populated with Ventoy's own bundled sample theme (background,
    menu/scrollbar pixmaps, a handful of icons) when it can be found —
    see _find_ventoy_sample_theme() — or with just a bare, empty
    theme.txt and an icons/ folder otherwise. Leaves any existing
    ventoy/theme/ or ventoy.json content untouched.

    Returns (ventoy_json_path, theme_dir, icons_dir), or (None, None, None)
    on failure.
    """
    theme_dir = os.path.join(mount_point, "ventoy", "theme")
    icons_dir = os.path.join(theme_dir, "icons")
    ventoy_json_path = os.path.join(mount_point, "ventoy", "ventoy.json")
    theme_txt_path = os.path.join(theme_dir, "theme.txt")

    try:
        if not os.path.isdir(theme_dir):
            sample = _find_ventoy_sample_theme()
            if sample:
                shutil.copytree(sample, theme_dir, dirs_exist_ok=True)

        os.makedirs(icons_dir, exist_ok=True)
        if not os.path.isfile(theme_txt_path):
            with open(theme_txt_path, "w", encoding="utf-8") as f:
                f.write("# Ventoy theme — created by VentoyIsoUpdater\n")

        data = load_ventoy_json(ventoy_json_path) if os.path.isfile(ventoy_json_path) else {}
        if not data.get("theme", {}).get("file"):
            data.setdefault("theme", {})["file"] = "/ventoy/theme/theme.txt"
            if not save_ventoy_json(ventoy_json_path, data):
                return None, None, None
    except OSError:
        return None, None, None

    return ventoy_json_path, theme_dir, icons_dir


def _normalize_folder(iso_folder: str) -> str:
    """"linux\\ubuntu", "/linux/ubuntu/", "." -> "/linux/ubuntu", "/" (Ventoy's
    path form: leading slash, no trailing slash, "/" for the drive root)."""
    p = iso_folder.replace("\\", "/").strip("/")
    return "/" + ("" if p in ("", ".") else p)


def get_existing_menu_classes(ventoy_json_path: str) -> dict[str, str]:
    """
    Returns a {folder -> class} dict from ventoy.json's "parent" menu_class
    entries — the only folder-based rule Ventoy applies to ISO files (see
    add_menu_class_entry).
    """
    data = load_ventoy_json(ventoy_json_path)
    result = {}
    for entry in data.get("menu_class", []):
        folder = entry.get("parent", "")
        cls = entry.get("class", "")
        if folder and cls:
            result[folder] = cls
    return result


def add_menu_class_entry(
    ventoy_json_path: str,
    iso_folder: str,
    grub_class: str,
) -> bool:
    """
    Adds a {"parent": folder, "class": grub_class} menu_class entry, giving
    every ISO directly inside iso_folder that class's icon.

    "parent", not "dir": Ventoy's GRUB module (ventoy_plugin_get_menu_class
    in ventoy_plugin.c) only matches ISO files against "key" (substring of
    the filename) and "parent" (exact containing folder) rules — a "dir"
    rule only ever styles the folder entry itself in TreeView mode, so ISOs
    covered by one still show the default vtoyiso icon.

    Does nothing if this folder already has a parent rule.
    Returns True if an entry was added.
    """
    if not ventoy_json_path or not os.path.isfile(ventoy_json_path):
        return False

    data = load_ventoy_json(ventoy_json_path)
    menu_class = data.get("menu_class", [])
    folder = _normalize_folder(iso_folder)

    if any(entry.get("parent") == folder for entry in menu_class):
        return False

    menu_class.append({"parent": folder, "class": grub_class})
    data["menu_class"] = menu_class
    return save_ventoy_json(ventoy_json_path, data)


# ── Logo management ───────────────────────────────────────────────────────────

def sync_logos(
    icons_dir: str,
    distros_db: dict,
    iso_entries: list,
    force: bool = False,
    ventoy_json_path: Optional[str] = None,
    on_progress: Optional[Callable] = None,
) -> dict[str, dict]:
    """
    Downloads missing logos for every distro among the detected ISOs.

    - Filename = grub_class + ".png" (e.g. "Zorin.png", "ubuntu.png")
    - If ventoy_json_path is given, also offers to add the missing
      menu_class entries to ventoy.json

    Returns {logo_filename: {"success": bool, "name": str, "error": str|None, "skipped": bool}}
    """
    if not os.path.isdir(icons_dir):
        return {}

    # Collects the unique classes among the ISOs present on the drive
    # Grouped by grub_class -> (logo_url, list of iso folders)
    needed: dict[str, dict] = {}
    for entry in iso_entries:
        if not entry.distro_id:
            continue
        distro_cfg = next(
            (d for d in distros_db.get("distros", []) if d["id"] == entry.distro_id),
            None
        )
        if not distro_cfg:
            continue
        grub_class = distro_cfg.get("grub_class")
        logo_url   = distro_cfg.get("logo_url")
        if not grub_class or not logo_url:
            continue
        if grub_class not in needed:
            needed[grub_class] = {
                "logo_url": logo_url,
                "name": distro_cfg["name"],
                "folders": set(),
            }
        # Collects the folders for the menu_class update (drive root included)
        needed[grub_class]["folders"].add(entry.folder or "/")

    results: dict[str, dict] = {}

    for grub_class, info in needed.items():
        logo_filename = grub_class + ".png"
        dest_path = os.path.join(icons_dir, logo_filename)

        # Already present and not forcing: considered OK without re-downloading
        if os.path.exists(dest_path) and not force:
            results[logo_filename] = {
                "success": True,
                "name": info["name"],
                "error": None,
                "skipped": True,
            }
        else:
            ok, err = download_logo(
                url=info["logo_url"],
                dest_path=dest_path,
                size=(128, 128),
            )
            results[logo_filename] = {
                "success": ok,
                "name": info["name"],
                "error": err,
                "skipped": False,
            }

        success = results[logo_filename]["success"]

        # Updates ventoy.json if requested and the logo succeeded
        if ventoy_json_path and success:
            # One rule per ISO folder (no-op for folders already mapped)
            for folder in info["folders"]:
                add_menu_class_entry(ventoy_json_path, folder, grub_class)

        if on_progress:
            on_progress(info["name"], success)

    return results


def get_missing_logos(icons_dir: str, iso_entries: list, distros_db: dict) -> list[str]:
    """
    Returns the list of logos (grub_class.png) missing from icons/
    for the ISOs present on the drive.
    """
    if not os.path.isdir(icons_dir):
        return []

    missing = []
    seen = set()
    for entry in iso_entries:
        distro_cfg = next(
            (d for d in distros_db.get("distros", []) if d["id"] == entry.distro_id),
            None
        ) if entry.distro_id else None
        if not distro_cfg:
            continue
        grub_class = distro_cfg.get("grub_class")
        if not grub_class or grub_class in seen:
            continue
        seen.add(grub_class)
        logo_file = grub_class + ".png"
        if not os.path.exists(os.path.join(icons_dir, logo_file)):
            missing.append(logo_file)
    return missing


def get_unmatched_isos(
    iso_entries: list,
    ventoy_json_path: str,
) -> list:
    """
    Returns the IsoEntry objects whose folder isn't covered by any
    menu_class rule. Useful for telling the user which ones have no icon.
    """
    if not ventoy_json_path:
        return []
    # Same rules Ventoy applies to ISO files: "key" = case-sensitive
    # substring of the filename, "parent" = exact containing folder
    rules = load_ventoy_json(ventoy_json_path).get("menu_class", [])
    keys = [r["key"] for r in rules if r.get("key")]
    parents = {r["parent"] for r in rules if r.get("parent")}

    return [
        entry for entry in iso_entries
        if _normalize_folder(entry.folder) not in parents
        and not any(k in entry.filename for k in keys)
    ]
