"""
Abstract base class for all distro version checkers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VersionInfo:
    version: str
    download_url: str
    filename: str
    checksum: Optional[str] = None
    checksum_type: Optional[str] = None  # 'sha256', 'md5', etc.
    release_notes_url: Optional[str] = None
    # Set when no automatic checksum is available but the source publishes a
    # manual verification method (OpenPGP signature, web verifier, etc.) —
    # the UI then invites the user to verify it themselves via this page
    # rather than implying an integrity check that didn't happen.
    # E.g.: Tails (OpenPGP signature only).
    manual_verify_url: Optional[str] = None
    # Optional info for the version browser
    variant_label: Optional[str] = None   # e.g. "Desktop", "Server", "Netinst"
    arch: str = "amd64"
    size_hint: Optional[str] = None       # e.g. "2.5 GB" if known
    stable: bool = True                   # False for non-LTS, rolling, beta versions


class BaseChecker(ABC):

    def __init__(self, variant: Optional[str] = None, arch: str = "amd64"):
        self.variant = variant
        # CPU architecture to fetch: "amd64" (x86_64, the default and
        # priority target) or "arm64" (aarch64) for the small set of
        # distros that publish a genuine, generic, Ventoy-bootable ARM64
        # ISO. Checkers that don't support "arm64" simply ignore it and
        # always serve amd64 — see CONTRIBUTING.md ("ARM64 entries") for the criteria used
        # to decide whether a distro gets an arm64 entry at all (a real
        # UEFI-bootable ISO, not a device-specific SBC image).
        self.arch = arch

    @abstractmethod
    def get_latest_version(self) -> Optional[VersionInfo]:
        """
        Queries the online source and returns info for the latest version.
        Returns None on failure or if not applicable.
        """
        pass

    def get_all_versions(self) -> list[VersionInfo]:
        """
        Returns every version available online for this distro/variant.
        Returns only the latest version by default.
        Override in subclasses to list the full history.
        """
        latest = self.get_latest_version()
        return [latest] if latest else []

    @abstractmethod
    def parse_local_version(self, filename: str) -> Optional[str]:
        """
        Extracts the version from a local ISO filename.
        Returns None if the file isn't recognized.
        """
        pass

    def is_outdated(self, local_version: str, latest_version: str) -> bool:
        """
        Compares two version strings.
        Returns True if local_version is lower than latest_version.
        """
        from packaging.version import Version, InvalidVersion
        try:
            return Version(local_version) < Version(latest_version)
        except InvalidVersion:
            # Fallback: lexicographic comparison (useful for YYYY.MM.DD dates)
            return local_version < latest_version
