"""
Download management with progress reporting and checksum verification.
"""

import os
import hashlib
import shutil
import threading
import zipfile
from typing import Callable, Optional

import requests


class DownloadError(Exception):
    pass


_IMAGE_EXTS = (".iso", ".img")


def _unwrap_disk_image_zip(zip_path: str) -> str:
    """
    Some sources (Memtest86+'s open-source build on memtest.org, notably)
    only publish their bootable image zipped up. Ventoy can only boot
    ISO/IMG/WIM/VHD(x)/EFI files, never a raw .zip, so a freshly downloaded
    archive whose sole member is a .iso/.img is transparently unwrapped
    here: the inner image replaces the .zip on disk under a name Ventoy can
    actually boot, instead of a dead file just sitting there.

    Returns the new path, or the original zip path unchanged if it isn't
    this specific single-image-file case (any other error also falls back
    to leaving the .zip as downloaded, rather than raising).
    """
    tmp_extract = None
    try:
        with zipfile.ZipFile(zip_path) as z:
            members = [n for n in z.namelist() if not n.endswith("/")]
            if len(members) != 1:
                return zip_path
            inner_name = members[0]
            ext = os.path.splitext(inner_name)[1].lower()
            if ext not in _IMAGE_EXTS:
                return zip_path

            # Prefer just stripping the trailing ".zip" ("foo.iso.zip" ->
            # "foo.iso"); fall back to appending the inner file's own
            # extension if that doesn't leave a recognized one.
            stripped = os.path.splitext(zip_path)[0]
            new_path = stripped if stripped.lower().endswith(_IMAGE_EXTS) \
                       else stripped + ext
            # Never overwrite an image already sitting on the drive under
            # that name: get a "_1", "_2"... suffixed name instead
            from core.iso_manager import get_download_path
            new_path = get_download_path(os.path.dirname(new_path),
                                         os.path.basename(new_path))

            tmp_extract = new_path + ".part"
            with z.open(inner_name) as src, open(tmp_extract, "wb") as dst:
                shutil.copyfileobj(src, dst)
    except (zipfile.BadZipFile, OSError, ValueError):
        if tmp_extract and os.path.exists(tmp_extract):
            try:
                os.remove(tmp_extract)
            except OSError:
                pass
        return zip_path

    os.replace(tmp_extract, new_path)
    os.remove(zip_path)
    return new_path


def download_file(
    url: str,
    dest_path: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
    checksum: Optional[str] = None,
    checksum_type: str = "sha256",
    chunk_size: int = 1024 * 1024,  # 1 MB
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """
    Downloads a file to dest_path.

    Args:
        url: source URL
        dest_path: destination path (full file path)
        on_progress: callback(bytes_downloaded, total_bytes) called on each chunk
        checksum: expected hash (optional)
        checksum_type: 'sha256' or 'md5'
        chunk_size: chunk size
        cancel_event: threading.Event to cancel the download

    Returns:
        Path of the downloaded file

    Raises:
        DownloadError on failure or cancellation
    """
    tmp_path = dest_path + ".part"

    try:
        with requests.get(url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            hasher = hashlib.new(checksum_type) if checksum else None

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if cancel_event and cancel_event.is_set():
                        raise DownloadError("Téléchargement annulé")
                    if chunk:
                        f.write(chunk)
                        if hasher:
                            hasher.update(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(downloaded, total)

        # Checksum verification
        if checksum and hasher:
            computed = hasher.hexdigest()
            if computed.lower() != checksum.lower():
                os.remove(tmp_path)
                raise DownloadError(
                    f"Checksum invalide : attendu {checksum}, obtenu {computed}"
                )

        # Atomic rename (os.replace is atomic on Linux, safe on Windows)
        os.replace(tmp_path, dest_path)

        if dest_path.lower().endswith(".zip"):
            return _unwrap_disk_image_zip(dest_path)
        return dest_path

    except DownloadError:
        raise
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise DownloadError(f"Erreur téléchargement : {e}") from e


def download_logo(
    url: str,
    dest_path: str,
    size: tuple[int, int] = (128, 128),
) -> tuple[bool, Optional[str]]:
    """
    Downloads (or copies) a logo and converts it to a resized PNG.

    Special ``local:<filename>`` prefix: copies from the project's
    assets/logos/ instead of downloading from the internet.

    Returns (True, None) on success, (False, "reason") on failure.
    """
    try:
        from PIL import Image
        import io

        _MAX_LOGO_BYTES = 5 * 1024 * 1024  # 5 MB

        if url.startswith("local:"):
            # File embedded in the project's assets/logos/
            filename = url[len("local:"):]
            assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logos")
            # Rejects any path traversal attempt (e.g. "local:../../etc/passwd"):
            # filename must name a file directly inside assets_dir, no separator.
            if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
                return False, "Nom de fichier local invalide"
            src = os.path.join(assets_dir, filename)
            if os.path.realpath(src) != os.path.join(os.path.realpath(assets_dir), filename):
                return False, "Nom de fichier local invalide"
            if not os.path.isfile(src):
                return False, f"Fichier local introuvable : {filename}"
            if os.path.getsize(src) > _MAX_LOGO_BYTES:
                return False, "Logo local trop volumineux (> 5 MB)"
            img = Image.open(src).convert("RGBA")
        else:
            resp = requests.get(url, stream=True, timeout=10)
            resp.raise_for_status()
            # Reads with a size limit to avoid memory exhaustion
            data = b""
            for chunk in resp.iter_content(chunk_size=65536):
                data += chunk
                if len(data) > _MAX_LOGO_BYTES:
                    return False, "Logo trop volumineux (> 5 MB)"
            img = Image.open(io.BytesIO(data)).convert("RGBA")

        img = img.resize(size, Image.LANCZOS)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        img.save(dest_path, "PNG")
        return True, None

    except requests.HTTPError as e:
        return False, f"HTTP {e.response.status_code if e.response else '?'}"
    except requests.ConnectionError:
        return False, "Connexion impossible"
    except requests.Timeout:
        return False, "Délai d'attente dépassé"
    except Exception as e:
        return False, str(e)[:80]
