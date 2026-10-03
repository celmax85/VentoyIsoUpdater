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

## Tests

```bash
.venv/bin/pytest tests/ -v
```

Any new logic in `core/` deserves a test in `tests/`. The `sources/`
checkers aren't unit-tested one by one (that would mean 64 network-dependent
test suites) — `tests/test_version_checker.py` and
`tests/test_base_checker.py` cover the shared contract (`BaseChecker`,
`VersionInfo`, orchestration).
