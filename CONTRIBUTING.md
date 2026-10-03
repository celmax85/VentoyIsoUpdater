🇬🇧 **English** · 🇫🇷 [Français](CONTRIBUTING.fr.md)

# Contributing

## Adding a distribution

See the [project structure](README.md#project-structure) in the README for
the general layout. Adding a distribution takes three changes:

1. A `sources/<distro>.py` file with a checker subclassing `BaseChecker`
   (`sources/base.py`).
2. Its import **and** its entry in the checker registry, in
   `core/version_checker.py::_load_checkers()` — easy to miss: without it,
   the distro is recognized on the drive but never checked for updates.
3. One or more entries in `data/distros.json` (one per edition or
   architecture).

`sources/` is the project's largest surface exposed to external
contributions: it's code that makes network requests to third-party sites
and applies regular expressions to filenames. Before opening a PR touching
`sources/` or `data/distros.json`, please check:

- **HTTPS only.** No `http://` URL unless technically unavoidable and
  documented in a comment (real case: `download.proxmox.com`'s TLS
  certificate doesn't match its own hostname — see `sources/proxmox.py`,
  which uses `enterprise.proxmox.com` instead).
- **`timeout=` is mandatory** on every `requests.get`/`head`/`post` call.
- **Never `shell=True`**, `eval`, `exec`, `pickle`, or `yaml.load`.
- **Real checksum whenever available.** If the source publishes a checksum
  file (`SHA256SUMS`, `CHECKSUM`, `*.sha256`…), populate `VersionInfo`'s
  `checksum=` field — not just `checksum_type=`, which alone triggers no
  verification at all (see `core/downloader.py`). The
  `sources/_checksum.py::fetch_sha256sums` / `fetch_bsd_sha256` helpers
  cover the two most common formats. If no checksum is published, leave
  both unset rather than implying a verification that doesn't happen.
- **No catastrophic-backtracking regexes.** Avoid nested patterns like
  `(.*)+` or `(\d+)+` applied to untrusted text (a filename scanned off the
  drive, an HTML response from a third-party site) — prefer precise
  character classes (`[\d.]+`, etc.), as the rest of `sources/` already
  does.
- **Log failures, don't just swallow them.** `except Exception: return
  None`/`[]` is the expected pattern (an unreachable source shouldn't crash
  the app), but add a `logger.debug(...)` call (see `core/logger.py`) so a
  real bug stays diagnosable instead of being indistinguishable from a
  source that's simply down.
- **`logo_url` must point to a raster image (PNG/JPG/WEBP/ICO), never
  `.svg`.** `core/downloader.py::download_logo` opens it with PIL, which
  can't decode vector formats — it fails with a fairly opaque
  `cannot identify image file` error at runtime, not at review time.
  A project's official site is often SVG-only for its logo; when that's
  the case, use a raster alternative (favicon, GitHub org avatar at
  `https://github.com/<org>.png`, etc.) — or if none looks good enough,
  bundle a real PNG under `assets/logos/` and reference it with the
  `local:<filename>` prefix, same as `proxmox.png` / `Pop!OS.png`.

### ARM64 entries

x86_64 (`amd64`) is the default and priority target. A distribution only
gets a separate ARM64 entry when its upstream source publishes a
**genuine, generic, UEFI-bootable ARM64 ISO** that Ventoy can boot:

- **Accepted:** an official `arm64`/`aarch64` ISO meant for any ARM64
  UEFI machine (e.g. Ubuntu Server, Debian, Rocky Linux, FreeBSD).
- **Rejected:** device-specific single-board-computer images (Raspberry
  Pi, Rockchip, Pinebook, per-SoC builds…): they aren't generically
  bootable the way an ISO is. Same for an "arm64" keyword that only
  appears elsewhere on a download page without a real ISO behind it.
  Check the actual file on the upstream server before adding an entry.

When a distribution qualifies:

1. The checker must branch on `self.arch` (`"amd64"` or `"arm64"`) to
   build the right URL, filename and checksum lookup. A checker that
   ignores `arch` always serves the x86_64 ISO, so an ARM64 entry
   pointing at it would silently download the wrong architecture.
2. Add a **separate** entry in `data/distros.json`: `id` suffixed with
   `_arm64`, `name` suffixed with ` (ARM64)`, `"checker_arch": "arm64"`,
   and the same `category` as its x86_64 sibling.
3. The `filename_patterns` of the two entries must not overlap: the
   x86_64 pattern has to reject the ARM64 filename (anchor it on
   `amd64`/`x86_64`), otherwise an ARM64 ISO on the drive is recognized
   as the x86_64 one.

## Tests

```bash
.venv/bin/pytest tests/ -v
```

Any new logic in `core/` deserves a test in `tests/`. The `sources/`
checkers aren't unit-tested one by one (that would mean 64 network-dependent
test suites) — `tests/test_version_checker.py` and
`tests/test_base_checker.py` cover the shared contract (`BaseChecker`,
`VersionInfo`, orchestration).
