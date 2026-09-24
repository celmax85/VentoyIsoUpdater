"""
Interface graphique principale — VentoyIsoUpdater
"""

import os
import shutil
import threading
import webbrowser
from typing import Callable, Optional

import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog

from gui.dialogs import show_info, show_warning, show_error, ask_yes_no

from core.ventoy_scanner import (
    find_ventoy_drives, scan_isos, load_distros_db,
    format_size, VentoyDrive, IsoEntry
)
from core.version_checker import check_all, CheckResult, UpdateStatus
from core.downloader import download_file, download_logo, DownloadError
from core.iso_manager import get_download_path, get_free_space, delete_iso, suggest_dest_folder
from core.theme_manager import sync_logos, get_missing_logos, get_unmatched_isos
import core.preferences as prefs
from core.logger import logger
from gui.i18n import t


STATUS_COLORS = {
    UpdateStatus.UP_TO_DATE: ("#2ecc71", "#27ae60"),
    UpdateStatus.OUTDATED:   ("#e74c3c", "#c0392b"),
    UpdateStatus.UNKNOWN:    ("#95a5a6", "#7f8c8d"),
    UpdateStatus.MANUAL:     ("#f39c12", "#d68910"),
    UpdateStatus.ERROR:      ("#e74c3c", "#c0392b"),
    UpdateStatus.CHECKING:   ("#3498db", "#2980b9"),
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def _patched_ctk_mouse_wheel_all(self, event):
    """
    Replaces CTkScrollableFrame's own global wheel handler.

    On Linux (and macOS), customtkinter scrolls by `-event.delta` "units"
    directly, with no scaling at all — it assumes `delta` is already a
    small number (±1, ±2...), which holds on native X11/macOS but not on
    every desktop: some GNOME/Wayland (via XWayland) setups report much
    larger values, so a single wheel notch could jump straight to the
    bottom or top of the list instead of scrolling a little. One "unit"
    per event, regardless of delta's magnitude, is what Button-4/Button-5
    (the X11 wheel events, unaffected by this) already do — this just
    makes <MouseWheel> match.
    """
    if self.check_if_master_is_canvas(event.widget):
        step = -1 if event.delta > 0 else 1
        if self._shift_pressed:
            if self._parent_canvas.xview() != (0.0, 1.0):
                self._parent_canvas.xview("scroll", step, "units")
        else:
            if self._parent_canvas.yview() != (0.0, 1.0):
                self._parent_canvas.yview("scroll", step, "units")


ctk.CTkScrollableFrame._mouse_wheel_all = _patched_ctk_mouse_wheel_all


# ══════════════════════════════════════════════════════════════════════════════
#  Cache de logos (miniatures PNG depuis le dossier icons/)
# ══════════════════════════════════════════════════════════════════════════════

class LogoCache:
    """Loads and caches PNG thumbnails from Ventoy's icons/ folder.
    Thread-safe: every cache operation is protected by a lock.
    """

    def __init__(self):
        self._dir: Optional[str] = None
        self._cache: dict[str, object] = {}
        self._lock = threading.Lock()

    def set_dir(self, icons_dir: Optional[str]):
        with self._lock:
            if icons_dir != self._dir:
                self._dir = icons_dir
                self._cache.clear()

    def get(self, grub_class: str, size: tuple[int, int] = (32, 32)) -> Optional[object]:
        """Retourne un CTkImage ou None si non disponible."""
        with self._lock:
            if not self._dir or not grub_class:
                return None
            key = f"{grub_class}_{size[0]}x{size[1]}"
            if key not in self._cache:
                path = os.path.join(self._dir, grub_class + ".png")
                if os.path.isfile(path):
                    try:
                        from PIL import Image
                        img = Image.open(path).convert("RGBA").resize(size, Image.LANCZOS)
                        self._cache[key] = ctk.CTkImage(light_image=img, dark_image=img, size=size)
                    except Exception as e:
                        logger.warning("LogoCache: impossible de charger %s : %s", path, e)
                        self._cache[key] = None
                else:
                    self._cache[key] = None
            return self._cache[key]

    def clear(self):
        with self._lock:
            self._cache.clear()


_logo_cache = LogoCache()


# ══════════════════════════════════════════════════════════════════════════════
#  Main window
# ══════════════════════════════════════════════════════════════════════════════

class VentoyIsoUpdaterApp(ctk.CTk):

    # Column widths shared between the header row (built once in
    # _build_ui) and every data row (_render_row), so both stay pixel-
    # aligned regardless of window width. Previously the header hardcoded
    # its own separate widths while each row stretched its filename
    # column (weight=1) to fill the remaining space — meaning every
    # column after it (local/latest version, status) drifted out from
    # under its header label, more so the wider the window. Order: logo,
    # folder, filename, size, local version, latest version. The final
    # column (status/actions) isn't listed here: it's sized to its own
    # content and left-anchored right after these, in header and rows
    # alike, so it never needs to match a fixed width.
    _TABLE_COL_WIDTHS = (38, 110, 320, 90, 100, 100)

    def __init__(self):
        # className sets WM_CLASS on X11/Wayland — needed so the window
        # manager associates the right icon via the .desktop file
        super().__init__(className="VentoyIsoUpdater")
        self.title("VentoyIsoUpdater")
        # Calculated minimum width: left panel 250 + _TABLE_COL_WIDTHS's
        # columns with their padding (46+118+328+98+108+108) + action column
        # ~300 + scrollbar/margins 60 = 1406 -> rounded down to 1280 as a
        # workable minimum (the action column can still wrap/crowd a little
        # below that). Height: toolbar 44 + header 32 + 10 rows × 40 +
        # progress bar 46 = 522 -> 580 comfortable minimum.
        self.minsize(1280, 580)

        self._prefs = prefs.load()
        geom = self._prefs.get("window_geometry") or "1280x720"
        self.geometry(geom)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            self.distros_db = load_distros_db()
        except Exception as e:
            logger.critical("Impossible de charger distros.json : %s", e)
            show_error(
                self, t('Erreur critique'),
                f"{t('Impossible de charger la base de données des distributions :\n')}{e}"
                f"{t('\n\nVérifiez que le fichier data/distros.json est présent et valide.')}"
            )
            self.distros_db = {"distros": []}

        self.current_drive: Optional[VentoyDrive] = None
        self.iso_entries: list[IsoEntry] = []
        self.check_results: dict[str, CheckResult] = {}
        self._cancel_event = threading.Event()
        self._drives: list[VentoyDrive] = []
        self._closing = False
        self._sync_in_progress = False
        self._refresh_in_progress = False

        self._build_ui()
        # Deferred until mainloop() runs: _refresh_drives hands its result
        # back from a worker thread via self.after(), which Tk silently
        # drops if the thread finishes before the event loop has started
        self.after(0, self._refresh_drives)
        logger.info(t('VentoyIsoUpdater démarré'))

    def _on_close(self):
        self._closing = True
        self._cancel_event.set()   # annule tout téléchargement en cours
        self._prefs["window_geometry"] = self.geometry()
        if self.current_drive:
            self._prefs["last_drive"] = self.current_drive.mount_point
        prefs.save(self._prefs)
        logger.info(t('VentoyIsoUpdater fermé'))
        self.destroy()

    # ─────────────────────────── BUILD UI ───────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── Panneau gauche ──────────────────────────────────────────────────
        left = ctk.CTkFrame(self, width=250, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_propagate(False)
        left.grid_rowconfigure(9, weight=1)

        ctk.CTkLabel(left, text="VentoyIsoUpdater",
                     font=ctk.CTkFont(size=18, weight="bold")
                     ).grid(row=0, column=0, padx=18, pady=(18, 6), sticky="w")

        ctk.CTkLabel(left, text=t('Clé Ventoy :'),
                     font=ctk.CTkFont(size=12)
                     ).grid(row=1, column=0, padx=18, pady=(8, 2), sticky="w")

        self.drive_combo = ctk.CTkComboBox(left, width=214,
                                            command=self._on_drive_selected)
        self.drive_combo.grid(row=2, column=0, padx=18, pady=2, sticky="ew")

        ctk.CTkButton(left, text=t('↺  Actualiser'),
                      command=self._refresh_drives, width=214
                      ).grid(row=3, column=0, padx=18, pady=(2, 10), sticky="ew")

        # Infos drive
        self.info_frame = ctk.CTkFrame(left, fg_color="transparent")
        self.info_frame.grid(row=4, column=0, padx=14, pady=0, sticky="new")
        self.lbl_info = {}
        for key, text in [("label", t('Label : —')), ("ver", t('Ventoy : —')),
                           ("space", t('Libre : —')), ("isos", t('ISO : —')), ("theme", t('Thème : —'))]:
            lbl = ctk.CTkLabel(self.info_frame, text=text, font=ctk.CTkFont(size=11), anchor="w")
            lbl.pack(anchor="w", pady=1)
            self.lbl_info[key] = lbl

        # Logos separator
        ctk.CTkLabel(left, text=t('Logos thème'),
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).grid(row=5, column=0, padx=18, pady=(16, 2), sticky="w")

        self.lbl_logos = ctk.CTkLabel(left, text="—", font=ctk.CTkFont(size=11),
                                       wraplength=210, anchor="w")
        self.lbl_logos.grid(row=6, column=0, padx=18, pady=2, sticky="w")

        self.btn_sync_logos = ctk.CTkButton(
            left, text=t('⬇  Télécharger logos manquants'), command=self._sync_logos,
            width=214, state="disabled"
        )
        self.btn_sync_logos.grid(row=7, column=0, padx=18, pady=2, sticky="ew")

        self.btn_force_logos = ctk.CTkButton(
            left, text=t('↺  Re-télécharger tous les logos'),
            command=lambda: self._sync_logos(force=True),
            width=214, state="disabled", fg_color="gray40"
        )
        self.btn_force_logos.grid(row=8, column=0, padx=18, pady=(0, 8), sticky="ew")

        # ── Panneau droit ────────────────────────────────────────────────────
        right = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # Barre d'outils
        toolbar = ctk.CTkFrame(right, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.btn_check_all = ctk.CTkButton(
            toolbar, text=t('🔍  Vérifier tout'),
            command=self._check_all, state="disabled"
        )
        self.btn_check_all.pack(side="left", padx=4)

        ctk.CTkButton(
            toolbar, text=t('⬇  Télécharger une ISO'),
            command=self._open_download_browser,
            fg_color="#27ae60", hover_color="#1e8449"
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            toolbar, text=t('💾  Créer une clé Ventoy'),
            command=self._open_ventoy_setup,
            fg_color="#8e44ad", hover_color="#6c3483"
        ).pack(side="left", padx=4)

        self.btn_theme_manager = ctk.CTkButton(
            toolbar, text=t('🎨  Gérer le thème'),
            command=self._open_theme_manager,
            fg_color="#1a5276", hover_color="#154360",
            state="disabled"
        )
        self.btn_theme_manager.pack(side="left", padx=4)

        ctk.CTkButton(
            toolbar, text=t('ℹ  À propos'),
            command=self._open_about,
            fg_color="gray35", hover_color="gray25", width=90
        ).pack(side="right", padx=4)

        from gui.i18n import SUPPORTED_LANGUAGES, get_language
        self._lang_by_label = {v: k for k, v in SUPPORTED_LANGUAGES.items()}
        self.lang_menu = ctk.CTkOptionMenu(
            toolbar, values=list(SUPPORTED_LANGUAGES.values()),
            width=110, command=self._on_language_change,
        )
        self.lang_menu.set(SUPPORTED_LANGUAGES.get(get_language(), "Français"))
        self.lang_menu.pack(side="right", padx=4)

        self.lbl_status = ctk.CTkLabel(toolbar, text="", font=ctk.CTkFont(size=12))
        self.lbl_status.pack(side="right", padx=8)

        # Table headers
        hdr = ctk.CTkFrame(right)
        hdr.grid(row=1, column=0, sticky="new")
        header_labels = ("", t('Dossier'), t('Fichier ISO'), t('Taille'),
                          t('Ver. locale'), t('Dernière ver.'))
        headers = list(zip(header_labels, self._TABLE_COL_WIDTHS)) + [(t('Statut'), 290)]
        for col, (txt, w) in enumerate(headers):
            ctk.CTkLabel(hdr, text=txt, font=ctk.CTkFont(size=12, weight="bold"), width=w
                         ).grid(row=0, column=col, padx=4, pady=4, sticky="w")

        # Tableau
        self.table = ctk.CTkScrollableFrame(right)
        self.table.grid(row=1, column=0, sticky="nsew", pady=(30, 0))
        self.table.grid_columnconfigure(0, weight=1)

        # Zone de progression (barre + label + bouton Annuler)
        prog_frame = ctk.CTkFrame(right, fg_color="transparent")
        prog_frame.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        prog_frame.grid_columnconfigure(0, weight=1)
        prog_frame.grid_remove()
        self._prog_frame = prog_frame

        self.progress_bar = ctk.CTkProgressBar(prog_frame)
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.progress_bar.set(0)

        self.btn_cancel_dl = ctk.CTkButton(
            prog_frame, text=t('✕ Annuler'), width=90, height=20,
            fg_color="#7f1c1c", hover_color="#a93226",
            font=ctk.CTkFont(size=11),
            command=self._cancel_download
        )
        self.btn_cancel_dl.grid(row=0, column=1, padx=(0, 4))

        self.lbl_progress = ctk.CTkLabel(right, text="", font=ctk.CTkFont(size=11))
        self.lbl_progress.grid(row=3, column=0, sticky="ew")
        self.lbl_progress.grid_remove()

    # ─────────────────────────── DRIVES ─────────────────────────────────────

    def _refresh_drives(self):
        # Detection shells out to lsblk/findmnt/udisksctl (with timeouts of
        # up to 15s each when mounting a freshly installed drive), so it
        # runs off the Tk thread to keep the window responsive.
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        self._set_status(t('Recherche des clés Ventoy...'))

        def run():
            try:
                drives = find_ventoy_drives()
            except Exception as e:
                logger.error("Drive detection failed: %s", e, exc_info=True)
                drives = []
            if not self._closing:
                self.after(0, lambda d=drives: self._apply_drives(d))

        threading.Thread(target=run, daemon=True).start()

    def _apply_drives(self, drives: list[VentoyDrive]):
        self._refresh_in_progress = False
        if not drives:
            self.drive_combo.configure(values=[t('Aucune clé Ventoy détectée')])
            self.drive_combo.set(t('Aucune clé Ventoy détectée'))
            self._set_status(t('Aucune clé Ventoy trouvée.'))
            return
        self._drives = drives
        labels = [f"{d.label}  ({d.mount_point})" for d in drives]
        self.drive_combo.configure(values=labels)
        # Restores the last-used drive if available
        last = self._prefs.get("last_drive", "")
        default_label = next(
            (lbl for lbl, d in zip(labels, drives) if d.mount_point == last),
            labels[0]
        )
        self.drive_combo.set(default_label)
        self._on_drive_selected(default_label)

    def _on_drive_selected(self, value: str):
        if not self._drives:
            return
        try:
            idx = list(self.drive_combo.cget("values")).index(value)
        except ValueError:
            return
        self.current_drive = self._drives[idx]
        self._scan_drive()

    def _scan_drive(self):
        if not self.current_drive:
            return
        d = self.current_drive
        self.iso_entries = scan_isos(d, self.distros_db)
        self.check_results.clear()

        self.lbl_info["label"].configure(text=f"{t('Label : ')}{d.label}")
        self.lbl_info["ver"].configure(text=f"{t('Ventoy : v')}{d.ventoy_version or t('inconnue')}")
        free = get_free_space(d.mount_point)
        self.lbl_info["space"].configure(text=f"{t('Libre : ')}{format_size(free)}")
        self.lbl_info["isos"].configure(text=f"{t('ISO : ')}{len(self.iso_entries)}{t(' fichier(s)')}")

        if d.theme_dir:
            theme_name = os.path.basename(d.theme_dir)
            self.lbl_info["theme"].configure(text=f"{t('Thème : ')}{theme_name}")
            _logo_cache.set_dir(d.theme_icons_dir)
            self.btn_theme_manager.configure(state="normal")

            if d.theme_icons_dir:
                missing = get_missing_logos(d.theme_icons_dir, self.iso_entries, self.distros_db)
                if missing:
                    self.lbl_logos.configure(
                        text=f"{len(missing)}{t(' logo(s) manquant(s) — cliquez pour télécharger')}",
                        text_color="#e74c3c")
                    self.btn_sync_logos.configure(state="normal")
                else:
                    self.lbl_logos.configure(
                        text=t('Tous les logos sont présents ✓'), text_color="#2ecc71")
                    self.btn_sync_logos.configure(state="disabled")
                self.btn_force_logos.configure(state="normal")
            else:
                self.lbl_logos.configure(text=t('Dossier icons/ absent du thème'), text_color="#f39c12")
                self.btn_sync_logos.configure(state="disabled")
                self.btn_force_logos.configure(state="disabled")
        else:
            _logo_cache.set_dir(None)
            self.lbl_info["theme"].configure(text=t('Thème : non trouvé'))
            self.lbl_logos.configure(text=t('Aucun thème détecté sur la clé'), text_color="#95a5a6")
            self.btn_sync_logos.configure(state="disabled")
            self.btn_force_logos.configure(state="disabled")
            # Left enabled: _open_theme_manager() offers to create a
            # default theme structure instead of the button being a dead
            # end on a drive that's never been booted from
            self.btn_theme_manager.configure(state="normal")

        self.btn_check_all.configure(state="normal" if self.iso_entries else "disabled")
        self._render_table()
        self._set_status(f"{len(self.iso_entries)}{t(' ISO trouvée(s). Cliquez sur \'Vérifier tout\'.')}")

    # ─────────────────────────── TABLE ──────────────────────────────────────

    def _bind_table_scroll(self, widget):
        """Propage la molette de tous les widgets enfants vers le canvas de la table."""
        canvas = self.table._parent_canvas
        # One "unit" per event regardless of e.delta's magnitude: on X11/
        # Wayland that value isn't reliably ±120 per notch the way it is on
        # Windows/macOS — some setups report much larger deltas, which made
        # a single wheel tick scroll the whole list to the bottom.
        widget.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"), add="+")
        widget.bind("<Button-4>",
                    lambda e: canvas.yview_scroll(-1, "units"), add="+")
        widget.bind("<Button-5>",
                    lambda e: canvas.yview_scroll(1, "units"), add="+")
        for child in widget.winfo_children():
            self._bind_table_scroll(child)

    def _render_table(self):
        for w in self.table.winfo_children():
            w.destroy()
        for i, iso in enumerate(self.iso_entries):
            self._render_row(i, iso, self.check_results.get(iso.filename))

    def _render_row(self, idx: int, iso: IsoEntry, result: Optional[CheckResult]):
        bg = "#2b2b2b" if idx % 2 == 0 else "#252525"
        row = ctk.CTkFrame(self.table, fg_color=bg, corner_radius=4)
        row.grid(row=idx, column=0, sticky="ew", padx=2, pady=1)

        w_logo, w_folder, w_file, w_size, w_localver, w_latestver = self._TABLE_COL_WIDTHS

        # Logo miniature
        logo_img = None
        if iso.distro_id:
            distro_cfg = next(
                (d for d in self.distros_db.get("distros", []) if d["id"] == iso.distro_id),
                None
            )
            if distro_cfg:
                logo_img = _logo_cache.get(distro_cfg.get("grub_class", ""), (32, 32))
        if logo_img:
            ctk.CTkLabel(row, image=logo_img, text="", width=w_logo
                         ).grid(row=0, column=0, padx=4, pady=4)
        else:
            ctk.CTkLabel(row, text="", width=w_logo).grid(row=0, column=0, padx=4, pady=4)

        ctk.CTkLabel(row, text=iso.folder, font=ctk.CTkFont(size=11),
                     width=w_folder, anchor="w").grid(row=0, column=1, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(row, text=iso.filename, font=ctk.CTkFont(size=11), width=w_file,
                     anchor="w", wraplength=w_file - 10).grid(row=0, column=2, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(row, text=format_size(iso.size_bytes) if iso.size_bytes else "—",
                     font=ctk.CTkFont(size=11), width=w_size
                     ).grid(row=0, column=3, padx=4, pady=4)
        ctk.CTkLabel(row, text=iso.local_version or "—",
                     font=ctk.CTkFont(size=11), width=w_localver
                     ).grid(row=0, column=4, padx=4, pady=4)

        latest_ver = result.latest_info.version if (result and result.latest_info) else "—"
        ctk.CTkLabel(row, text=latest_ver,
                     font=ctk.CTkFont(size=11), width=w_latestver
                     ).grid(row=0, column=5, padx=4, pady=4)

        action_frame = ctk.CTkFrame(row, fg_color="transparent")
        action_frame.grid(row=0, column=6, padx=4, pady=4, sticky="w")

        if result:
            color = STATUS_COLORS.get(result.status, ("#95a5a6", "#7f8c8d"))[0]
            ctk.CTkLabel(action_frame, text=t(result.status.value),
                         fg_color=color, corner_radius=6,
                         font=ctk.CTkFont(size=10, weight="bold"),
                         padx=8, pady=2
                         ).pack(side="left", padx=4)

            if result.status == UpdateStatus.OUTDATED and result.latest_info:
                ctk.CTkButton(
                    action_frame, text=t('⬇ Télécharger'),
                    width=100, height=26,
                    fg_color="#27ae60", hover_color="#1e8449",
                    command=lambda i=iso, r=result: self._download_latest(i, r)
                ).pack(side="left", padx=2)

            elif result.status == UpdateStatus.MANUAL:
                checker = self._get_checker(iso)
                if checker and hasattr(checker, "get_homepage"):
                    ctk.CTkButton(
                        action_frame, text="🌐", width=32, height=26,
                        fg_color="#3498db",
                        command=lambda u=checker.get_homepage(): webbrowser.open(u)
                    ).pack(side="left", padx=2)

            # Bouton parcourir les versions (toutes distros reconnues non-Windows)
            if iso.distro_id and not iso.distro_id.startswith("windows"):
                ctk.CTkButton(
                    action_frame, text="☰", width=32, height=26,
                    fg_color="gray40",
                    command=lambda i=iso: self._open_version_browser(i)
                ).pack(side="left", padx=2)
        else:
            ctk.CTkLabel(action_frame, text=t('En attente'),
                         fg_color="#444", corner_radius=6,
                         font=ctk.CTkFont(size=10), padx=8, pady=2
                         ).pack(side="left", padx=4)
            if iso.distro_id and not iso.distro_id.startswith("windows"):
                ctk.CTkButton(
                    action_frame, text=t('☰ Versions'), width=80, height=26,
                    fg_color="gray40",
                    command=lambda i=iso: self._open_version_browser(i)
                ).pack(side="left", padx=2)

        # Bouton supprimer — toujours disponible, même avant toute vérification
        # de mise à jour (pas besoin d'avoir cliqué "Vérifier tout" pour
        # pouvoir retirer une ISO de la clé).
        ctk.CTkButton(
            action_frame, text="🗑", width=32, height=26,
            fg_color="#7f1c1c", hover_color="#a93226",
            command=lambda i=iso: self._confirm_delete(i)
        ).pack(side="left", padx=2)

        # Propage la molette depuis tous les enfants de la ligne vers le canvas
        self._bind_table_scroll(row)

    def _update_row(self, iso: IsoEntry, result: CheckResult):
        self.check_results[iso.filename] = result
        for w in self.table.winfo_children():
            info = w.grid_info()
            r = int(info.get("row", -1))
            if 0 <= r < len(self.iso_entries) and self.iso_entries[r].filename == iso.filename:
                w.destroy()
                self._render_row(r, iso, result)
                return

    # ─────────────────────────── CHECK ALL ──────────────────────────────────

    def _check_all(self):
        if not self.iso_entries:
            return
        self.btn_check_all.configure(state="disabled", text=t('Vérification...'))
        self._set_status(t('Vérification en cours...'))

        for iso in self.iso_entries:
            self._update_row(iso, CheckResult(iso=iso, status=UpdateStatus.CHECKING))

        def run():
            def on_result(res):
                self.after(0, lambda r=res: self._update_row(r.iso, r))
            check_all(self.iso_entries, self.distros_db, on_result=on_result)
            self.after(0, self._on_check_done)

        threading.Thread(target=run, daemon=True).start()

    def _on_check_done(self):
        outdated = sum(1 for r in self.check_results.values()
                       if r.status == UpdateStatus.OUTDATED)
        self.btn_check_all.configure(state="normal", text=t('🔍  Vérifier tout'))
        self._set_status(
            f"{t('✓ Terminé — ')}{outdated}{t(' ISO(s) à télécharger.')}" if outdated
            else t('✓ Terminé — Toutes les ISO reconnues sont à jour.')
        )

    # ─────────────────────────── DOWNLOAD LATEST ────────────────────────────

    def _download_latest(self, iso: IsoEntry, result: CheckResult):
        """Downloads the latest version into the same folder, without removing the old one."""
        if not result.latest_info or not self.current_drive:
            return
        info = result.latest_info
        dest_folder = os.path.dirname(iso.path)
        dest_path = get_download_path(dest_folder, info.filename)
        confirmed = ask_yes_no(
            self, t('Confirmer le téléchargement'),
            t('Mettre à jour :\n\n  Actuel  :  ') + iso.filename +
            t('\n  Nouveau :  ') + info.filename +
            t('\n\n  Version : ') + info.version +
            (f"  ({info.variant_label})" if info.variant_label else "") +
            t('\n\nLe fichier sera téléchargé dans :\n') + dest_folder +
            t("\n\nL'ancienne ISO ne sera PAS supprimée automatiquement.")
        )
        if not confirmed:
            return
        self._do_download(
            url=info.download_url,
            dest_path=dest_path,
            label=info.filename,
            on_done=lambda: self._scan_drive(),
            distro_id=iso.distro_id,
            checksum=info.checksum,
            checksum_type=info.checksum_type,
            manual_verify_url=info.manual_verify_url,
        )

    # ─────────────────────────── VERSION BROWSER (par ISO) ──────────────────

    def _open_version_browser(self, iso: IsoEntry):
        """Opens the version browser for an existing ISO on the drive."""
        distro_cfg = next(
            (d for d in self.distros_db.get("distros", []) if d["id"] == iso.distro_id),
            None
        )
        if not distro_cfg:
            return
        dest_folder = os.path.dirname(iso.path)
        VersionBrowserDialog(self, distro_cfg, self.distros_db, dest_folder,
                             on_download=self._do_download)

    # ─────────────────────────── DOWNLOAD BROWSER (global) ─────────────────

    def _open_download_browser(self):
        """Opens the global download browser (with no ISO selected)."""
        if not self.current_drive:
            show_info(self, "Info", t('Sélectionnez d\'abord une clé Ventoy.'))
            return
        DistroPickerDialog(self, self.distros_db, self.current_drive,
                           iso_entries=self.iso_entries,
                           on_download=self._do_download,
                           on_done=self._scan_drive)

    # ─────────────────────────── GENERIC DOWNLOAD ───────────────────────────

    def _do_download(self, url: str, dest_path: str, label: str,
                     on_done: Optional[Callable] = None,
                     on_error: Optional[Callable] = None,
                     on_progress: Optional[Callable[[int, int], None]] = None,
                     distro_id: Optional[str] = None,
                     checksum: Optional[str] = None,
                     checksum_type: Optional[str] = None,
                     manual_verify_url: Optional[str] = None):
        """
        Downloads a file with progress reporting, without blocking the UI.
        `on_error`, unlike `on_done`, fires on any failure — used by the
        batch queue (DistroPickerDialog) to move on to the next ISO instead
        of silently stalling when one download in the queue fails.
        `on_progress(done, total)`, if given, is called alongside the main
        window's own progress bar — used by the batch queue to show
        per-item progress in its queue panel.
        """
        self._show_progress(True)
        self.btn_check_all.configure(state="disabled")
        logger.info("Download started: %s -> %s", url, dest_path)

        # Set by run() to whichever of on_done/on_error applies, and fired
        # from the single _finish() callback below — never scheduled
        # separately from hiding the progress bar. Queuing them as two
        # independent self.after(0, ...) calls let the batch queue's next
        # download show its own progress bar (from on_done, processed
        # first) only to have this download's own "hide progress" call
        # (processed second) immediately hide it again — the "first item's
        # progress shows, later ones don't" bug. Bundling both in one
        # callback makes that ordering impossible.
        result_callback = None

        def run():
            nonlocal result_callback
            try:
                # ── Available disk space check ──────────────
                try:
                    import requests as _req
                    head = _req.head(url, timeout=6, allow_redirects=True)
                    content_length = int(head.headers.get("content-length", 0))
                    if content_length > 0:
                        dest_dir = os.path.dirname(dest_path) or "."
                        free = get_free_space(dest_dir)
                        if free < content_length:
                            needed = format_size(content_length)
                            avail  = format_size(free)
                            msg = (t('Espace insuffisant sur le disque :\n  Nécessaire : ') + needed +
                                   t('\n  Disponible : ') + avail)
                            logger.warning("Not enough space: %s needed, %s available",
                                           needed, avail)
                            if not self._closing:
                                self.after(0, lambda m=msg: show_error(
                                    self, t('Espace insuffisant'), m))
                                result_callback = on_error
                            return
                except Exception as e:
                    logger.debug("Disk space check skipped: %s", e)

                def _track_progress(done, total):
                    if total > 0 and not self._closing:
                        self.after(0, lambda: self._update_progress(
                            done / total,
                            f"{label} — {format_size(done)} / {format_size(total)}"
                        ))
                        if on_progress:
                            self.after(0, lambda: on_progress(done, total))

                # download_file() returns the final path: usually dest_path
                # unchanged, but some sources (Memtest86+) only publish
                # their image zipped up, and it transparently unwraps that
                # to the actual .iso/.img Ventoy can boot.
                final_path = download_file(
                    url=url, dest_path=dest_path,
                    on_progress=_track_progress,
                    cancel_event=self._cancel_event,
                    checksum=checksum,
                    checksum_type=checksum_type or "sha256",
                )
                logger.info("Download complete: %s", os.path.basename(final_path))

                if not self._closing:
                    self.after(0, lambda: self._set_status(
                        f"{t('✓ Téléchargé : ')}{os.path.basename(final_path)}"))

                # No automatic checksum for this source: invites a manual
                # verification instead of implying the integrity was
                # checked when it wasn't.
                if manual_verify_url and not checksum and not self._closing:
                    self.after(0, lambda u=manual_verify_url: show_info(
                        self, t('Vérification manuelle recommandée'),
                        f"{os.path.basename(final_path)}" +
                        t(" a été téléchargé.\n\nCette source ne publie pas d'empreinte automatiquement vérifiable — pensez à contrôler l'intégrité de l'ISO vous-même avant de l'utiliser :\n\n") +
                        u
                    ))

                if distro_id and self.current_drive and not self._closing:
                    self._register_iso_folder(distro_id, final_path)
                    if self.current_drive.theme_icons_dir:
                        self._auto_download_logo(distro_id, final_path)

                result_callback = on_done

            except DownloadError as e:
                logger.warning("Download failed: %s", e)
                if not self._closing:
                    self.after(0, lambda err=str(e): show_error(self, t('Erreur'), t(err)))
                result_callback = on_error
            except Exception as e:
                logger.error("Erreur inattendue dans _do_download : %s", e, exc_info=True)
                if not self._closing:
                    self.after(0, lambda err=str(e): show_error(
                        self, t('Erreur inattendue'), err))
                result_callback = on_error
            finally:
                if not self._closing:
                    def _finish(cb=result_callback):
                        self._show_progress(False)
                        self.btn_check_all.configure(state="normal")
                        if cb:
                            cb()
                    self.after(0, _finish)

        threading.Thread(target=run, daemon=True).start()

    def _register_iso_folder(self, distro_id: str, iso_dest_path: str):
        """
        Registers the ISO's folder in ventoy.json (menu_class).
        Always called after any successful ISO download.
        Runs on the download thread.
        """
        drive = self.current_drive
        if not drive or not drive.ventoy_json_path:
            return

        distro_cfg = next(
            (d for d in self.distros_db.get("distros", []) if d["id"] == distro_id),
            None
        )
        if not distro_cfg:
            return

        grub_class = distro_cfg.get("grub_class")
        if not grub_class:
            return

        iso_folder = os.path.relpath(
            os.path.dirname(iso_dest_path), drive.mount_point
        ).replace("\\", "/")

        from core.theme_manager import add_menu_class_entry
        added = add_menu_class_entry(drive.ventoy_json_path, iso_folder, grub_class)
        if added:
            self.after(0, lambda: self._set_status(
                f"{t('✓ ventoy.json mis à jour : /')}{iso_folder} → {grub_class}"
            ))

    def _auto_download_logo(self, distro_id: str, iso_dest_path: str):
        """
        Downloads the distro's logo into icons/ if it's missing.
        The folder has already been registered in ventoy.json by _register_iso_folder.
        Runs on the download thread — only touches the UI via after().
        """
        drive = self.current_drive
        if not drive or not drive.theme_icons_dir:
            return

        distro_cfg = next(
            (d for d in self.distros_db.get("distros", []) if d["id"] == distro_id),
            None
        )
        if not distro_cfg:
            return

        grub_class = distro_cfg.get("grub_class")
        logo_url   = distro_cfg.get("logo_url")
        if not grub_class or not logo_url:
            return

        logo_path = os.path.join(drive.theme_icons_dir, grub_class + ".png")
        if os.path.isfile(logo_path):
            return  # déjà présent

        ok, err = download_logo(url=logo_url, dest_path=logo_path, size=(128, 128))
        if not ok:
            self.after(0, lambda: self._set_status(
                f"{t('⚠ Logo introuvable pour ')}{distro_cfg['name']} : {err}"
            ))
            return

        _logo_cache.clear()
        self.after(0, lambda: self._set_status(
            f"{t('✓ Logo téléchargé : ')}{grub_class}.png"
        ))

    # ─────────────────────────── DELETE ISO ─────────────────────────────────

    def _confirm_delete(self, iso: IsoEntry):
        if ask_yes_no(
            self, t('Supprimer'),
            f"{t('Supprimer définitivement :\n')}{iso.filename} ?",
            danger=True,
        ):
            if delete_iso(iso.path):
                self._scan_drive()
            else:
                show_error(self, t('Erreur'), t('Impossible de supprimer le fichier.'))

    # ─────────────────────────── LOGO SYNC ──────────────────────────────────

    def _sync_logos(self, force: bool = False):
        if self._sync_in_progress:
            return
        if not self.current_drive or not self.current_drive.theme_icons_dir:
            show_info(self, "Logos", t('Aucun dossier icons/ détecté sur la clé.'))
            return
        self._sync_in_progress = True
        self.btn_sync_logos.configure(state="disabled", text=t('Téléchargement en cours...'))
        self.btn_force_logos.configure(state="disabled")

        def run():
            drive = self.current_drive
            results = sync_logos(
                icons_dir=drive.theme_icons_dir,
                distros_db=self.distros_db,
                iso_entries=self.iso_entries,
                force=force,
                ventoy_json_path=drive.ventoy_json_path,
            )
            ok   = sum(1 for v in results.values() if v["success"])
            fail = sum(1 for v in results.values() if not v["success"])
            downloaded = sum(1 for v in results.values() if not v["skipped"] and v["success"])
            msg = f"{ok}{t(' logo(s) OK')}"
            if downloaded:
                msg += f" ({downloaded}{t(' téléchargé(s))')}"
            if fail:
                msg += f", {fail}{t(' échec(s)')}"

            # Reloads the cache after syncing
            _logo_cache.clear()

            # Checks whether any ISOs still lack a menu_class entry
            unmatched = get_unmatched_isos(self.iso_entries, drive.ventoy_json_path)
            if unmatched:
                names = ", ".join(e.filename for e in unmatched[:3])
                if len(unmatched) > 3:
                    names += f" (+{len(unmatched) - 3})"
                msg += f"{t('\n⚠ Sans icône : ')}{names}"

            color = "#2ecc71" if not fail and not unmatched else "#e74c3c" if fail else "#f39c12"
            self.after(0, lambda: self.lbl_logos.configure(text=msg, text_color=color))
            self._sync_in_progress = False
            self.after(0, lambda: self.btn_sync_logos.configure(
                state="normal", text=t('⬇  Télécharger logos manquants')))
            self.after(0, lambda: self.btn_force_logos.configure(state="normal"))
            self.after(0, lambda: self._set_status(f"{t('Logos : ')}{ok} OK" + (f", {fail}{t(' échec(s)')}" if fail else "")))
            self.after(0, lambda: self._render_table())
            # Opens the log if any failures or downloads occurred
            if fail or downloaded:
                self.after(0, lambda r=results: SyncLogDialog(self, r))

        threading.Thread(target=run, daemon=True).start()

    # ─────────────────────────── HELPERS ────────────────────────────────────

    def _open_about(self):
        AboutDialog(self)

    def _on_language_change(self, label: str):
        """
        Changes the preferred language and invites a restart.
        Doesn't rebuild the UI on the fly: the main window and a dozen
        dialogs are already instantiated with their text fixed at
        construction time — rebuilding them live would be far more
        fragile than simply re-reading the preference on next launch.
        """
        lang = self._lang_by_label.get(label, "fr")
        prefs.set_key("language", lang)
        show_info(
            self, t('Langue'),
            t("La langue sera appliquée au prochain démarrage de VentoyIsoUpdater."),
        )

    def _open_ventoy_setup(self):
        VentoySetupDialog(self, on_done=self._refresh_drives)

    def _open_theme_manager(self):
        if not self.current_drive:
            return
        if not self.current_drive.theme_dir:
            if not ask_yes_no(
                self, t('Aucun thème configuré'),
                t("Cette clé Ventoy n'a pas encore de thème configuré — normal pour une "
                  "installation qui n'a jamais démarré : rien ne le crée automatiquement.\n\n"
                  "Créer une structure de thème par défaut pour commencer à la personnaliser ?"),
            ):
                return
            from core.theme_manager import create_default_theme
            ventoy_json_path, theme_dir, icons_dir = create_default_theme(self.current_drive.mount_point)
            if not theme_dir:
                show_error(self, t('Erreur'), t("Impossible de créer la structure du thème."))
                return
            self.current_drive.ventoy_json_path = ventoy_json_path
            self.current_drive.theme_dir = theme_dir
            self.current_drive.theme_icons_dir = icons_dir
            self._scan_drive()
        ThemeEditorDialog(self, self.current_drive, self.distros_db,
                          iso_entries=self.iso_entries,
                          on_change=lambda: (self._render_table(), _logo_cache.clear()))

    def _get_checker(self, iso: IsoEntry):
        from core.version_checker import _load_checkers, _CHECKER_REGISTRY
        _load_checkers()
        cfg = next((d for d in self.distros_db.get("distros", [])
                    if d["id"] == iso.distro_id), None)
        if not cfg:
            return None
        cls = _CHECKER_REGISTRY.get(cfg.get("checker"))
        return cls(variant=cfg.get("checker_variant"), arch=cfg.get("checker_arch", "amd64")) if cls else None

    def _set_status(self, text: str):
        self.lbl_status.configure(text=text)

    def _cancel_download(self):
        self._cancel_event.set()
        self.btn_cancel_dl.configure(state="disabled", text=t('Annulation…'))
        logger.info(t('Téléchargement annulé par l\'utilisateur'))

    def _show_progress(self, visible: bool):
        if visible:
            self._cancel_event.clear()
            self.btn_cancel_dl.configure(state="normal", text=t('✕ Annuler'))
            self._prog_frame.grid()
            self.lbl_progress.grid()
        else:
            self.progress_bar.set(0)
            self._prog_frame.grid_remove()
            self.lbl_progress.grid_remove()

    def _update_progress(self, value: float, label: str):
        self.progress_bar.set(value)
        self.lbl_progress.configure(text=label)


# ══════════════════════════════════════════════════════════════════════════════
#  Dialog: version browser for a specific distro
# ══════════════════════════════════════════════════════════════════════════════

class VersionBrowserDialog(ctk.CTkToplevel):
    """
    Window showing every version available online for a distro.
    Lets the user download any version into the folder of their choice.
    """

    def __init__(self, parent, distro_cfg: dict, distros_db: dict,
                 default_dest: str, on_download):
        super().__init__(parent)
        self.title(f"{t('Versions disponibles — ')}{distro_cfg['name']}")
        self.geometry("680x500")
        self.after(100, self.lift)
        self.after(150, self.focus_force)

        self.distro_cfg = distro_cfg
        self.distros_db = distros_db
        self.default_dest = default_dest
        self.on_download = on_download
        self._versions = []
        self._stable_only = ctk.BooleanVar(value=True)
        self._page = 0

        self._build()
        self._load_versions()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Titre + toggle stable
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, padx=12, pady=(12, 4), sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(top, text=f"{t('Versions de ')}{self.distro_cfg['name']}",
                     font=ctk.CTkFont(size=15, weight="bold")
                     ).grid(row=0, column=0, sticky="w")

        ctk.CTkCheckBox(top, text=t('Stables uniquement'),
                        variable=self._stable_only,
                        command=self._refresh_list,
                        font=ctk.CTkFont(size=12)
                        ).grid(row=0, column=1, padx=8, sticky="e")

        self.lbl_loading = ctk.CTkLabel(self, text=t('Chargement des versions...'),
                                         font=ctk.CTkFont(size=12))
        self.lbl_loading.grid(row=1, column=0, padx=16, pady=8)

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self.scroll.grid_columnconfigure(0, weight=1)
        self.scroll.grid_remove()

        # Dossier de destination
        dest_frame = ctk.CTkFrame(self, fg_color="transparent")
        dest_frame.grid(row=2, column=0, padx=8, pady=6, sticky="ew")
        dest_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(dest_frame, text=t('Dossier :'), font=ctk.CTkFont(size=11)
                     ).grid(row=0, column=0, padx=6)
        self.dest_var = ctk.StringVar(value=self.default_dest)
        ctk.CTkEntry(dest_frame, textvariable=self.dest_var, font=ctk.CTkFont(size=11)
                     ).grid(row=0, column=1, padx=4, sticky="ew")
        ctk.CTkButton(dest_frame, text="📁", width=36,
                      command=self._browse_dest
                      ).grid(row=0, column=2, padx=4)

        ctk.CTkButton(self, text=t('Fermer'), command=self.destroy, width=100
                      ).grid(row=3, column=0, pady=(0, 10))

    def _load_versions(self):
        def run():
            from core.version_checker import _load_checkers, _CHECKER_REGISTRY
            _load_checkers()
            checker_id = self.distro_cfg.get("checker")
            variant = self.distro_cfg.get("checker_variant")
            arch = self.distro_cfg.get("checker_arch", "amd64")
            cls = _CHECKER_REGISTRY.get(checker_id)
            if not cls:
                self.after(0, lambda: self.winfo_exists() and
                           self.lbl_loading.configure(text=t('Checker non disponible.')))
                return
            checker = cls(variant=variant, arch=arch)
            versions = checker.get_all_versions()
            self.after(0, lambda v=versions: self.winfo_exists() and self._show_versions(v))

        threading.Thread(target=run, daemon=True).start()

    def _refresh_list(self):
        """Re-applies the stable/all filter to the already-loaded list."""
        self._page = 0
        if self._versions:
            self._render_versions(self._versions)

    def _show_versions(self, versions):
        if not self.winfo_exists():
            return
        self._versions = versions
        self.lbl_loading.grid_remove()
        self.scroll.grid()
        self._render_versions(versions)

    _PAGE_SIZE = 20

    def _render_versions(self, versions):
        for w in self.scroll.winfo_children():
            w.destroy()

        filtered = [v for v in versions if not self._stable_only.get() or v.stable]

        if not filtered:
            msg = t('Aucune version stable trouvée.\nDésactivez le filtre pour voir toutes les versions.') \
                  if self._stable_only.get() and versions else t('Aucune version trouvée.')
            ctk.CTkLabel(self.scroll, text=msg,
                         font=ctk.CTkFont(size=12), wraplength=400
                         ).grid(row=0, column=0, padx=12, pady=8)
            return

        total_pages = max(1, (len(filtered) + self._PAGE_SIZE - 1) // self._PAGE_SIZE)
        self._page = max(0, min(self._page, total_pages - 1))
        page_items = filtered[self._page * self._PAGE_SIZE:(self._page + 1) * self._PAGE_SIZE]

        for i, v in enumerate(page_items):
            bg = "#2b2b2b" if i % 2 == 0 else "#252525"
            row = ctk.CTkFrame(self.scroll, fg_color=bg, corner_radius=4)
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            row.grid_columnconfigure(1, weight=1)

            label = v.version
            if v.variant_label:
                label += f"  [{v.variant_label}]"
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=12),
                         anchor="w").grid(row=0, column=0, padx=10, pady=6, sticky="w")

            ctk.CTkLabel(row, text=v.filename,
                         font=ctk.CTkFont(size=10), text_color="gray70",
                         anchor="w").grid(row=0, column=1, padx=6, pady=6, sticky="w")

            ctk.CTkButton(
                row, text=t('⬇ Télécharger'), width=110, height=28,
                fg_color="#27ae60", hover_color="#1e8449",
                command=lambda vi=v: self._download_version(vi)
            ).grid(row=0, column=2, padx=8, pady=4)

        # Pagination bar (shown only when there's more than one page)
        if total_pages > 1:
            nav = ctk.CTkFrame(self.scroll, fg_color="transparent")
            nav.grid(row=len(page_items), column=0, pady=(8, 4))
            ctk.CTkButton(
                nav, text=t('← Précédent'), width=110,
                state="normal" if self._page > 0 else "disabled",
                command=lambda: self._go_page(self._page - 1)
            ).grid(row=0, column=0, padx=6)
            ctk.CTkLabel(
                nav,
                text=f"Page {self._page + 1} / {total_pages}  ({len(filtered)} versions)",
                font=ctk.CTkFont(size=11)
            ).grid(row=0, column=1, padx=10)
            ctk.CTkButton(
                nav, text=t('Suivant →'), width=110,
                state="normal" if self._page < total_pages - 1 else "disabled",
                command=lambda: self._go_page(self._page + 1)
            ).grid(row=0, column=2, padx=6)

    def _go_page(self, page: int):
        self._page = page
        self._render_versions(self._versions)

    def _download_version(self, v):
        dest_folder = self.dest_var.get()
        if not os.path.isdir(dest_folder):
            try:
                os.makedirs(dest_folder, exist_ok=True)
            except Exception as e:
                show_error(self, t('Erreur'), f"{t('Impossible de créer le dossier :\n')}{dest_folder}\n{e}")
                return
        from core.iso_manager import get_download_path
        dest_path = get_download_path(dest_folder, v.filename)
        self.on_download(url=v.download_url, dest_path=dest_path, label=v.filename,
                         distro_id=self.distro_cfg.get("id"),
                         checksum=v.checksum, checksum_type=v.checksum_type,
                         manual_verify_url=v.manual_verify_url)
        self.destroy()

    def _browse_dest(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(initialdir=self.dest_var.get(), parent=self)
        if folder:
            self.dest_var.set(folder)


# ══════════════════════════════════════════════════════════════════════════════
#  Dialog: distro + version picker (global "Download an ISO" button)
# ══════════════════════════════════════════════════════════════════════════════

class DistroPickerDialog(ctk.CTkToplevel):
    """
    Global download window:
    1. Pick a distro from the list
    2. See every available version
    3. Pick the destination folder on the drive
    4. Download
    """

    def __init__(self, parent, distros_db: dict, drive: VentoyDrive,
                 iso_entries: list, on_download, on_done):
        super().__init__(parent)
        self.title(t('Télécharger une ISO'))
        self.geometry("820x540")
        self.after(100, self.lift)
        self.after(150, self.focus_force)

        self.distros_db = distros_db
        self.drive = drive
        self._iso_entries = iso_entries
        self.on_download = on_download
        self.on_done = on_done
        self._versions = []
        self._stable_only = ctk.BooleanVar(value=True)
        self._queue: list[dict] = []
        self._batch_running = False
        self._active_progress_label: Optional[ctk.CTkLabel] = None

        self.protocol("WM_DELETE_WINDOW", self._on_request_close)
        self._build()

    # Category order and labels
    _CAT_ORDER  = ["linux", "bsd", "security", "gaming", "server", "tools", "windows", "other"]
    _CAT_LABELS = {
        "linux":    "Linux",
        "bsd":      "BSD",
        "security": t('Sécurité'),
        "gaming":   "Gaming",
        "server":   t('Serveur / Réseau'),
        "tools":    t('Outils'),
        "windows":  "Windows",
        "other":    t('Autre'),
    }

    # Couleurs des boutons de la liste
    _COL = {
        "normal":        ("transparent", "#2a2a3e"),   # (fg, hover)
        "installed":     ("#1a3320",     "#254030"),
        "selected":      ("#1a3264",     "#1e3a78"),
        "sel_installed": ("#1a4040",     "#1e4a50"),
    }

    def _build(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._installed_ids = {e.distro_id for e in self._iso_entries if e.distro_id}
        self._selected_id: Optional[str] = None
        self._distro_btns: dict[str, ctk.CTkButton] = {}

        # ── Liste des distros ────────────────────────────────────────────────
        left = ctk.CTkFrame(self, width=240)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left.grid_propagate(False)
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Distro",
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._populate_distro_list())
        ctk.CTkEntry(
            left, textvariable=self._search_var,
            placeholder_text=t('🔍 Rechercher...'),
            font=ctk.CTkFont(size=12), height=30
        ).grid(row=1, column=0, padx=8, pady=(0, 4), sticky="ew")

        self._distro_scroll = ctk.CTkScrollableFrame(left)
        self._distro_scroll.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        self._distro_scroll.grid_columnconfigure(0, weight=1)

        # ── Panneau versions (construit une seule fois) ──────────────────────
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        title_bar = ctk.CTkFrame(right, fg_color="transparent")
        title_bar.grid(row=0, column=0, padx=4, pady=(8, 2), sticky="ew")
        title_bar.grid_columnconfigure(0, weight=1)

        self.lbl_distro_title = ctk.CTkLabel(
            title_bar, text=t('← Sélectionnez une distro'),
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.lbl_distro_title.grid(row=0, column=0, sticky="w")

        ctk.CTkCheckBox(title_bar, text=t('Stables uniquement'),
                        variable=self._stable_only,
                        command=self._refresh_versions,
                        font=ctk.CTkFont(size=12)
                        ).grid(row=0, column=1, padx=8, sticky="e")

        self.versions_frame = ctk.CTkScrollableFrame(right)
        self.versions_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self.versions_frame.grid_columnconfigure(0, weight=1)

        # ── File d'attente (téléchargements sélectionnés) ─────────────────────
        queue_outer = ctk.CTkFrame(right, fg_color="#1c1c2a", corner_radius=6)
        queue_outer.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))
        queue_outer.grid_columnconfigure(0, weight=1)

        queue_header_bar = ctk.CTkFrame(queue_outer, fg_color="transparent")
        queue_header_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        queue_header_bar.grid_columnconfigure(0, weight=1)

        self.lbl_queue_header = ctk.CTkLabel(
            queue_header_bar, text=t("File d'attente (0)"),
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.lbl_queue_header.grid(row=0, column=0, sticky="w")

        self.btn_download_queue = ctk.CTkButton(
            queue_header_bar, text=t('⬇ Télécharger tout'), width=150,
            fg_color="#27ae60", hover_color="#1e8449",
            state="disabled", command=self._start_batch_download
        )
        self.btn_download_queue.grid(row=0, column=1, padx=(8, 0))

        self.queue_list = ctk.CTkScrollableFrame(queue_outer, height=90,
                                                  fg_color="transparent")
        self.queue_list.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        self.queue_list.grid_columnconfigure(0, weight=1)

        dest_frame = ctk.CTkFrame(right, fg_color="transparent")
        dest_frame.grid(row=3, column=0, padx=4, pady=6, sticky="ew")
        dest_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(dest_frame, text=t('Dossier :'), font=ctk.CTkFont(size=11)
                     ).grid(row=0, column=0, padx=6)
        self.dest_var = ctk.StringVar(value=self.drive.mount_point)
        ctk.CTkEntry(dest_frame, textvariable=self.dest_var,
                     font=ctk.CTkFont(size=11)
                     ).grid(row=0, column=1, padx=4, sticky="ew")
        ctk.CTkButton(dest_frame, text="📁", width=36,
                      command=self._browse_dest
                      ).grid(row=0, column=2, padx=4)

        self.btn_close = ctk.CTkButton(right, text=t('Fermer'),
                                       command=self._on_request_close, width=100)
        self.btn_close.grid(row=4, column=0, pady=(0, 8))

        self._populate_distro_list()
        self._render_queue()

    # ── Scroll molette : forward tous les events vers le canvas interne ──────

    def _bind_scroll_to(self, widget, canvas):
        """Recursively forwards the mouse wheel to the given canvas."""
        # One "unit" per event regardless of e.delta's magnitude — see
        # _bind_table_scroll's comment.
        widget.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"),
                    add="+")
        widget.bind("<Button-4>",
                    lambda e: canvas.yview_scroll(-1, "units"),
                    add="+")
        widget.bind("<Button-5>",
                    lambda e: canvas.yview_scroll(1, "units"),
                    add="+")
        for child in widget.winfo_children():
            self._bind_scroll_to(child, canvas)

    def _bind_scroll(self, widget):
        try:
            self._bind_scroll_to(widget, self._distro_scroll._parent_canvas)
        except AttributeError:
            pass

    def _bind_versions_scroll(self, widget):
        try:
            self._bind_scroll_to(widget, self.versions_frame._parent_canvas)
        except AttributeError:
            pass

    def _populate_distro_list(self):
        """Reconstruit uniquement la liste de distros (filtre de recherche)."""
        query = self._search_var.get().strip().lower()
        for w in self._distro_scroll.winfo_children():
            w.destroy()
        self._distro_btns.clear()

        categories: dict[str, list] = {}
        for d in self.distros_db.get("distros", []):
            if query and query not in d["name"].lower():
                continue
            cat = d.get("category", "other")
            categories.setdefault(cat, []).append(d)

        for cat in categories:
            categories[cat].sort(key=lambda d: d["name"].lower())

        if not categories:
            ctk.CTkLabel(self._distro_scroll, text=t('Aucun résultat.'),
                         font=ctk.CTkFont(size=12), text_color="gray60"
                         ).grid(row=0, column=0, padx=12, pady=8)
            return

        row_idx = 0
        for cat in self._CAT_ORDER + sorted(set(categories) - set(self._CAT_ORDER)):
            if cat not in categories:
                continue
            cat_lbl = self._CAT_LABELS.get(cat, cat).upper()
            lbl = ctk.CTkLabel(
                self._distro_scroll, text=cat_lbl,
                font=ctk.CTkFont(size=10, weight="bold"), text_color="gray55"
            )
            lbl.grid(row=row_idx, column=0, padx=8, pady=(10, 2), sticky="w")
            self._bind_scroll(lbl)
            row_idx += 1

            for d in categories[cat]:
                installed = d["id"] in self._installed_ids
                is_sel    = d["id"] == self._selected_id

                if is_sel:
                    col_key = "sel_installed" if installed else "selected"
                else:
                    col_key = "installed" if installed else "normal"
                fg, hov = self._COL[col_key]

                prefix = "▶  " if is_sel else ("✓  " if installed else "   ")
                text_col = (
                    "#2ecc71" if installed and not is_sel else
                    "#ffffff" if is_sel else
                    ("gray75" if d.get("checker") == "windows" else "white")
                )

                btn = ctk.CTkButton(
                    self._distro_scroll,
                    text=prefix + d["name"], anchor="w",
                    fg_color=fg, hover_color=hov,
                    text_color=text_col,
                    font=ctk.CTkFont(size=12),
                    corner_radius=6,
                    command=lambda cfg=d: self._select_distro(cfg)
                )
                btn.grid(row=row_idx, column=0, sticky="ew", padx=4, pady=1)
                self._distro_btns[d["id"]] = btn
                self._bind_scroll(btn)
                row_idx += 1

    def _select_distro(self, distro_cfg: dict):
        # ── Visual update of the selection ────────────────────────────
        prev_id = self._selected_id
        new_id  = distro_cfg["id"]

        # Resets the old button to its normal state
        if prev_id and prev_id in self._distro_btns:
            inst = prev_id in self._installed_ids
            fg, hov = self._COL["installed" if inst else "normal"]
            prefix = "✓  " if inst else "   "
            prev_name = next(
                (d["name"] for d in self.distros_db.get("distros", []) if d["id"] == prev_id),
                prev_id
            )
            tc = "#2ecc71" if inst else "white"
            self._distro_btns[prev_id].configure(
                fg_color=fg, hover_color=hov,
                text_color=tc, text=prefix + prev_name
            )

        # Puts the new button in the selected state
        self._selected_id = new_id
        if new_id in self._distro_btns:
            inst = new_id in self._installed_ids
            fg, hov = self._COL["sel_installed" if inst else "selected"]
            self._distro_btns[new_id].configure(
                fg_color=fg, hover_color=hov,
                text_color="#ffffff",
                text="▶  " + distro_cfg["name"]
            )

        self.lbl_distro_title.configure(
            text=f"{t('Versions de ')}{distro_cfg['name']}")
        for w in self.versions_frame.winfo_children():
            w.destroy()

        # Updates the suggested destination folder for this distro
        suggested = suggest_dest_folder(
            self.drive.mount_point, distro_cfg, self._iso_entries
        )
        self.dest_var.set(suggested)

        ctk.CTkLabel(self.versions_frame, text=t('Chargement...'),
                     font=ctk.CTkFont(size=12)
                     ).grid(row=0, column=0, padx=12, pady=8)

        # Windows → lien direct
        if distro_cfg.get("checker") == "windows":
            for w in self.versions_frame.winfo_children():
                w.destroy()
            ctk.CTkLabel(
                self.versions_frame,
                text=t('Les ISO Windows doivent être téléchargées\nmanuellement depuis le site Microsoft.'),
                font=ctk.CTkFont(size=12), wraplength=400
            ).grid(row=0, column=0, padx=12, pady=8)
            ctk.CTkButton(
                self.versions_frame, text=t('🌐 Ouvrir le site Microsoft'),
                command=lambda: webbrowser.open(distro_cfg.get("homepage", ""))
            ).grid(row=1, column=0, padx=12, pady=4)
            return

        def run():
            from core.version_checker import _load_checkers, _CHECKER_REGISTRY
            _load_checkers()
            checker_id = distro_cfg.get("checker")
            cls = _CHECKER_REGISTRY.get(checker_id)
            if not cls:
                self.after(0, lambda c=distro_cfg:
                           self.winfo_exists() and self._show_versions([], c))
                return
            checker = cls(variant=distro_cfg.get("checker_variant"),
                          arch=distro_cfg.get("checker_arch", "amd64"))
            versions = checker.get_all_versions()
            self.after(0, lambda v=versions, c=distro_cfg:
                       self.winfo_exists() and self._show_versions(v, c))

        threading.Thread(target=run, daemon=True).start()

    def _show_versions(self, versions, distro_cfg: dict):
        if not self.winfo_exists():
            return
        self._versions = versions
        self._render_versions()

    def _refresh_versions(self):
        if self._versions:
            self._render_versions()

    def _render_versions(self):
        for w in self.versions_frame.winfo_children():
            w.destroy()

        filtered = [v for v in self._versions if not self._stable_only.get() or v.stable]

        if not filtered:
            msg = t('Aucune version stable.\nDésactivez le filtre pour voir toutes les versions.') \
                  if self._stable_only.get() and self._versions \
                  else t('Aucune version trouvée ou source inaccessible.')
            ctk.CTkLabel(self.versions_frame, text=msg,
                         font=ctk.CTkFont(size=12), wraplength=350
                         ).grid(row=0, column=0, padx=12, pady=8)
            return

        for i, v in enumerate(filtered):
            bg = "#2b2b2b" if i % 2 == 0 else "#252525"
            row = ctk.CTkFrame(self.versions_frame, fg_color=bg, corner_radius=4)
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            row.grid_columnconfigure(1, weight=1)

            label = v.version
            if v.variant_label:
                label += f"  [{v.variant_label}]"
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=12),
                         anchor="w").grid(row=0, column=0, padx=10, pady=5, sticky="w")
            ctk.CTkLabel(row, text=v.filename,
                         font=ctk.CTkFont(size=10), text_color="gray70",
                         anchor="w").grid(row=0, column=1, padx=6, pady=5, sticky="w")
            already_queued = any(
                item['distro_id'] == self._selected_id and item['version'].filename == v.filename
                for item in self._queue
            )
            ctk.CTkButton(
                row, text=("✓" if already_queued else "➕"), width=36, height=26,
                fg_color=("#3a3a3a" if already_queued else "#27ae60"),
                hover_color=("#3a3a3a" if already_queued else "#1e8449"),
                state=("disabled" if already_queued else "normal"),
                command=lambda vi=v: self._add_to_queue(vi)
            ).grid(row=0, column=2, padx=8, pady=3)
            self._bind_versions_scroll(row)

    def _add_to_queue(self, v):
        """Adds this version to the batch download queue instead of downloading it right away."""
        dest_folder = self.dest_var.get()
        if not os.path.isdir(dest_folder):
            try:
                os.makedirs(dest_folder, exist_ok=True)
            except Exception as e:
                show_error(self, t('Erreur'), f"{t('Impossible de créer le dossier :\n')}{dest_folder}\n{e}")
                return
        from core.iso_manager import get_download_path
        dest_path = get_download_path(dest_folder, v.filename)
        distro_name = next(
            (d["name"] for d in self.distros_db.get("distros", []) if d["id"] == self._selected_id),
            self._selected_id
        )
        self._queue.append({
            'distro_id': self._selected_id,
            'distro_name': distro_name,
            'version': v,
            'dest_path': dest_path,
        })
        self._render_versions()
        self._render_queue()

    def _remove_from_queue(self, index: int):
        if 0 <= index < len(self._queue):
            del self._queue[index]
            self._render_versions()
            self._render_queue()

    def _bind_queue_scroll(self, widget):
        try:
            self._bind_scroll_to(widget, self.queue_list._parent_canvas)
        except AttributeError:
            pass

    def _render_queue(self):
        for w in self.queue_list.winfo_children():
            w.destroy()
        self._active_progress_label = None

        if not self._queue:
            ctk.CTkLabel(self.queue_list, text=t('Aucune ISO sélectionnée.'),
                         font=ctk.CTkFont(size=11), text_color="gray60"
                         ).grid(row=0, column=0, padx=6, pady=4, sticky="w")
        else:
            for i, item in enumerate(self._queue):
                is_active = self._batch_running and i == 0
                bg = "#274361" if is_active else "#232338"
                row = ctk.CTkFrame(self.queue_list, fg_color=bg, corner_radius=4)
                row.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
                row.grid_columnconfigure(1, weight=1)

                ctk.CTkLabel(row, text=item['distro_name'],
                             font=ctk.CTkFont(size=11, weight="bold"),
                             anchor="w").grid(row=0, column=0, padx=(8, 4), pady=4, sticky="w")
                ctk.CTkLabel(row, text=item['version'].filename,
                             font=ctk.CTkFont(size=10), text_color="gray70",
                             anchor="w").grid(row=0, column=1, padx=4, pady=4, sticky="w")

                if is_active:
                    lbl = ctk.CTkLabel(
                        row, text="⏳ …", width=64,
                        font=ctk.CTkFont(size=11, weight="bold"), text_color="#5dade2"
                    )
                    lbl.grid(row=0, column=2, padx=6, pady=2)
                    self._active_progress_label = lbl
                else:
                    ctk.CTkButton(
                        row, text="✕", width=24, height=22,
                        fg_color="#8b2020", hover_color="#a52a2a",
                        state=("disabled" if self._batch_running else "normal"),
                        command=lambda idx=i: self._remove_from_queue(idx)
                    ).grid(row=0, column=2, padx=6, pady=2)
                self._bind_queue_scroll(row)

        self.lbl_queue_header.configure(text=t("File d'attente") + f" ({len(self._queue)})")
        self.btn_download_queue.configure(
            state=("normal" if self._queue and not self._batch_running else "disabled")
        )

    def _on_queue_progress(self, done: int, total: int):
        """Updates the progress indicator on the queue's currently downloading row."""
        if not self.winfo_exists() or not self._active_progress_label:
            return
        try:
            if not self._active_progress_label.winfo_exists():
                return
        except Exception:
            return
        pct = int(done / total * 100) if total else 0
        self._active_progress_label.configure(text=f"⏳ {pct}%")

    def _start_batch_download(self):
        if not self._queue or self._batch_running:
            return
        self._batch_running = True
        self.btn_download_queue.configure(state="disabled")
        self.btn_close.configure(state="disabled")
        self._render_queue()
        self._process_next_in_queue()

    def _process_next_in_queue(self):
        if not self.winfo_exists():
            return
        if not self._queue:
            self._batch_running = False
            self.btn_close.configure(state="normal")
            self._render_queue()
            self._refresh_versions()
            self._refresh_installed_state()
            if self.on_done:
                self.on_done()
            return

        # Left at the front of the queue (not popped yet) so it stays
        # visible, shown as "downloading", until it actually finishes —
        # instead of vanishing the instant it starts.
        item = self._queue[0]
        self._render_queue()
        self._refresh_versions()
        v = item['version']
        dest_folder = os.path.dirname(item['dest_path'])
        if not os.path.isdir(dest_folder):
            try:
                os.makedirs(dest_folder, exist_ok=True)
            except Exception:
                pass

        def _advance():
            if self._queue and self._queue[0] is item:
                self._queue.pop(0)
            self._process_next_in_queue()

        self.on_download(
            url=v.download_url, dest_path=item['dest_path'], label=v.filename,
            on_done=_advance,
            on_error=_advance,
            on_progress=self._on_queue_progress,
            distro_id=item['distro_id'],
            checksum=v.checksum, checksum_type=v.checksum_type,
            manual_verify_url=v.manual_verify_url,
        )

    def _refresh_installed_state(self):
        """
        Re-scans the drive right after a batch finishes, so distros just
        downloaded immediately show up as installed (✓, highlighted) in the
        left-hand list — without having to close and reopen this dialog to
        see it.
        """
        if not self.winfo_exists() or not self.drive:
            return
        try:
            from core.ventoy_scanner import scan_isos
            self._iso_entries = scan_isos(self.drive, self.distros_db)
        except Exception as e:
            logger.debug("DistroPickerDialog: installed-state refresh skipped: %s", e)
            return
        self._installed_ids = {e.distro_id for e in self._iso_entries if e.distro_id}
        self._populate_distro_list()

    def _on_request_close(self):
        if self._batch_running:
            show_info(self, "Info",
                      t("Le téléchargement de la file en cours doit se terminer avant de pouvoir fermer cette fenêtre."))
            return
        self.destroy()

    def _browse_dest(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(
            initialdir=self.drive.mount_point, parent=self
        )
        if folder:
            self.dest_var.set(folder)


# ══════════════════════════════════════════════════════════════════════════════
#  Dialog: create / install Ventoy on a blank USB drive
# ══════════════════════════════════════════════════════════════════════════════

class VentoySetupDialog(ctk.CTkToplevel):
    """
    Guides the user through creating a Ventoy drive from scratch:
    1. Detects available USB drives
    2. Downloads the latest Ventoy version if needed
    3. Runs the installation (pkexec/sudo on Linux)
    """

    def __init__(self, parent, on_done: callable = None):
        super().__init__(parent)
        self.title(t('Créer une clé Ventoy'))
        self.resizable(True, True)
        self.after(100, self.lift)
        self.after(150, self.focus_force)

        self.on_done = on_done
        self._ventoy_dir: Optional[str] = None   # dossier ventoy extrait
        self._ventoy_version: Optional[str] = None
        self._tmp_dir: Optional[str] = None
        self._selected_device: Optional[str] = None

        self._build()

        # Sized from the content's actual required height rather than a
        # fixed guess: on a higher DPI/UI-scaling setting, every widget
        # this dialog builds grows accordingly, and a fixed "620x520" could
        # end up taller than the window itself — clipping the install
        # button at the bottom with no way to reach it, since the dialog
        # used to be non-resizable. Still resizable above as a fallback for
        # any scaling this doesn't fully account for.
        self.update_idletasks()
        width = max(620, self.winfo_reqwidth())
        height = min(self.winfo_reqheight() + 20, self.winfo_screenheight() - 80)
        self.minsize(min(560, width), min(460, height))
        self.geometry(f"{width}x{height}")

        self._scan_drives()
        self._check_ventoy()

    # ─────────────────────── BUILD ──────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)  # log_frame (scrollable) absorbs extra space

        # ── Header ──────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="#2c1654", corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text=t('💾  Créer une clé Ventoy'),
            font=ctk.CTkFont(size=16, weight="bold"), text_color="white"
        ).pack(padx=18, pady=12, anchor="w")
        ctk.CTkLabel(
            header,
            text=t('Ventoy vous permet de démarrer plusieurs ISO depuis une seule clé USB.'),
            font=ctk.CTkFont(size=11), text_color="#c9a8f0"
        ).pack(padx=18, pady=(0, 12), anchor="w")

        # ── USB drive selection ───────────────────────────────────────────────
        usb_frame = ctk.CTkFrame(self)
        usb_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(12, 6))
        usb_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(usb_frame, text=t('Clé USB :'),
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w", columnspan=2)

        self.drive_combo = ctk.CTkComboBox(usb_frame, width=400,
                                            command=self._on_drive_selected)
        self.drive_combo.grid(row=1, column=0, padx=12, pady=(0, 6), sticky="ew", columnspan=2)

        ctk.CTkButton(usb_frame, text=t('↺ Actualiser'), width=110,
                      command=self._scan_drives
                      ).grid(row=2, column=0, padx=12, pady=(0, 10), sticky="w")

        self.lbl_drive_info = ctk.CTkLabel(
            usb_frame, text="", font=ctk.CTkFont(size=11), text_color="gray70"
        )
        self.lbl_drive_info.grid(row=2, column=1, padx=12, pady=(0, 10), sticky="w")

        # ── Ventoy ───────────────────────────────────────────────────────────
        vtoy_frame = ctk.CTkFrame(self)
        vtoy_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        vtoy_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(vtoy_frame, text=t('Version Ventoy :'),
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w", columnspan=2)

        self.lbl_ventoy_status = ctk.CTkLabel(
            vtoy_frame, text=t('Recherche en cours...'),
            font=ctk.CTkFont(size=12), text_color="gray70"
        )
        self.lbl_ventoy_status.grid(row=1, column=0, padx=12, pady=(0, 4), sticky="w")

        self.btn_download_ventoy = ctk.CTkButton(
            vtoy_frame, text=t('⬇  Télécharger Ventoy'),
            command=self._download_ventoy,
            fg_color="#2980b9", hover_color="#1f618d", state="disabled"
        )
        self.btn_download_ventoy.grid(row=1, column=1, padx=12, pady=(0, 4), sticky="e")

        self.vtoy_progress = ctk.CTkProgressBar(vtoy_frame)
        self.vtoy_progress.grid(row=2, column=0, columnspan=2,
                                padx=12, pady=(0, 10), sticky="ew")
        self.vtoy_progress.set(0)
        self.vtoy_progress.grid_remove()

        # ── Avertissement + bouton installer ─────────────────────────────────
        warn_frame = ctk.CTkFrame(self, fg_color="#3a1f00")
        warn_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=6)
        ctk.CTkLabel(
            warn_frame,
            text=t("⚠  ATTENTION : L'installation de Ventoy effacera TOUTES les données\n   de la clé USB sélectionnée. Cette opération est irréversible."),
            font=ctk.CTkFont(size=11), text_color="#f39c12", justify="left"
        ).pack(padx=14, pady=10, anchor="w")

        # ── Log de sortie ─────────────────────────────────────────────────────
        log_section = ctk.CTkFrame(self, fg_color="transparent")
        log_section.grid(row=4, column=0, sticky="nsew", padx=16, pady=4)
        log_section.grid_columnconfigure(0, weight=1)
        log_section.grid_rowconfigure(1, weight=1)

        log_header = ctk.CTkFrame(log_section, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ctk.CTkLabel(
            log_header, text=t('Journal :'),
            font=ctk.CTkFont(size=11, weight="bold"), text_color="gray70"
        ).pack(side="left")
        self.btn_copy_log = ctk.CTkButton(
            log_header, text=t('📋  Copier'), width=90, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#3a3a3a", hover_color="#4a4a4a",
            command=self._copy_log,
        )
        self.btn_copy_log.pack(side="right")

        self.log_frame = ctk.CTkScrollableFrame(log_section, height=80)
        self.log_frame.grid(row=1, column=0, sticky="nsew")
        self.log_frame.grid_columnconfigure(0, weight=1)
        self.lbl_log = ctk.CTkLabel(
            self.log_frame, text="", font=ctk.CTkFont(size=10, family="monospace"),
            text_color="gray70", anchor="w", justify="left", wraplength=560
        )
        self.lbl_log.pack(anchor="w")

        # ── Boutons bas ───────────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=5, column=0, pady=(6, 12))

        self.btn_install = ctk.CTkButton(
            btn_row, text=t('💾  Installer Ventoy'),
            command=self._confirm_install,
            fg_color="#8e44ad", hover_color="#6c3483",
            width=180, height=36, state="disabled"
        )
        self.btn_install.pack(side="left", padx=8)

        ctk.CTkButton(btn_row, text=t('Fermer'), command=self.destroy,
                      width=100, height=36).pack(side="left", padx=8)

    # ─────────────────────── DRIVES ─────────────────────────────────────────

    def _scan_drives(self):
        from core.ventoy_installer import find_usb_drives
        self.lbl_drive_info.configure(text=t('Scan des clés USB...'))
        drives = find_usb_drives(exclude_ventoy=True)
        if not drives:
            self.drive_combo.configure(values=[t('Aucune clé USB détectée')])
            self.drive_combo.set(t('Aucune clé USB détectée'))
            self.lbl_drive_info.configure(text=t('Branchez une clé USB et actualisez.'))
            self._selected_device = None
        else:
            labels = [d.display for d in drives]
            self.drive_combo.configure(values=labels)
            self.drive_combo.set(labels[0])
            self._drives = drives
            self._on_drive_selected(labels[0])
        self._update_install_btn()

    def _on_drive_selected(self, value: str):
        if not hasattr(self, "_drives") or not self._drives:
            return
        try:
            idx = list(self.drive_combo.cget("values")).index(value)
            self._selected_device = self._drives[idx].device
            d = self._drives[idx]
            from core.ventoy_scanner import format_size
            self.lbl_drive_info.configure(
                text=f"→ {d.device}  |  {format_size(d.size_bytes)}"
            )
        except (ValueError, IndexError):
            pass
        self._update_install_btn()

    # ─────────────────────── VENTOY CHECK / DOWNLOAD ────────────────────────

    def _check_ventoy(self):
        def run():
            from core.ventoy_installer import (
                find_ventoy_binary, find_cached_ventoy, get_ventoy_latest_version
            )
            # System install first, then a previously downloaded copy — so
            # closing and reopening this wizard doesn't forget it and force
            # a redundant re-download every time.
            binary = find_ventoy_binary() or find_cached_ventoy()
            version = get_ventoy_latest_version()
            self._ventoy_version = version
            if not self.winfo_exists():
                return
            if binary:
                self._ventoy_dir = os.path.dirname(binary)
                msg = f"{t('✓  Ventoy trouvé : ')}{binary}"
                if version:
                    msg += f"{t('\n   Dernière version disponible : v')}{version}"
                self.after(0, lambda m=msg: self.winfo_exists() and
                           self.lbl_ventoy_status.configure(text=m, text_color="#2ecc71"))
                self.after(0, lambda: self.winfo_exists() and self._update_install_btn())
            else:
                msg = t('Ventoy non trouvé localement.')
                if version:
                    msg += f"{t('  Dernière version : v')}{version}"
                self.after(0, lambda m=msg: self.winfo_exists() and
                           self.lbl_ventoy_status.configure(text=m, text_color="#e67e22"))
                self.after(0, lambda: self.winfo_exists() and
                           self.btn_download_ventoy.configure(state="normal"))

        threading.Thread(target=run, daemon=True).start()

    def _download_ventoy(self):
        if not self._ventoy_version:
            show_error(self, t('Erreur'), t('Impossible de récupérer la version Ventoy.'))
            return
        self.btn_download_ventoy.configure(state="disabled", text=t('Téléchargement...'))
        self.vtoy_progress.grid()
        self.vtoy_progress.set(0)

        def run():
            from core.ventoy_installer import download_ventoy, get_ventoy_cache_dir
            self._tmp_dir = get_ventoy_cache_dir()

            def on_progress(done, total):
                self.after(0, lambda d=done, t=total:
                           self.winfo_exists() and self.vtoy_progress.set(d / t))

            extracted = download_ventoy(
                version=self._ventoy_version,
                dest_dir=self._tmp_dir,
                on_progress=on_progress
            )
            if not self.winfo_exists():
                return
            if extracted:
                from core.ventoy_installer import find_ventoy_in_dir
                script = find_ventoy_in_dir(extracted)
                if script:
                    self._ventoy_dir = os.path.dirname(script)
                    ver = self._ventoy_version
                    self.after(0, lambda v=ver: self.winfo_exists() and
                               self.lbl_ventoy_status.configure(
                                   text=f"{t('✓  Ventoy v')}{v}{t(' prêt à l\'installation.')}",
                                   text_color="#2ecc71"))
                    self.after(0, lambda: self.winfo_exists() and self._update_install_btn())
                else:
                    self.after(0, lambda: self.winfo_exists() and
                               self.lbl_ventoy_status.configure(
                                   text=t('Erreur : ventoy2disk.sh introuvable dans l\'archive.'),
                                   text_color="#e74c3c"))
            else:
                self.after(0, lambda: self.winfo_exists() and
                           self.lbl_ventoy_status.configure(
                               text=t("Échec du téléchargement ou de la vérification d'intégrité de l'archive. Réessayez."),
                               text_color="#e74c3c"))

            self.after(0, lambda: self.winfo_exists() and self.vtoy_progress.grid_remove())
            self.after(0, lambda: self.winfo_exists() and self.btn_download_ventoy.configure(
                state="normal", text=t('⬇  Télécharger Ventoy')))

        threading.Thread(target=run, daemon=True).start()

    # ─────────────────────── INSTALL ─────────────────────────────────────────

    def _update_install_btn(self):
        ready = bool(self._selected_device and self._ventoy_dir)
        self.btn_install.configure(state="normal" if ready else "disabled")

    def _confirm_install(self):
        if not self._selected_device or not self._ventoy_dir:
            return
        from core.ventoy_installer import find_ventoy_in_dir
        script = find_ventoy_in_dir(self._ventoy_dir) or self._ventoy_dir
        answer = ask_yes_no(
            self, t('Confirmer l\'installation'),
            f"{t('Installer Ventoy sur :\n\n  ')}{self._selected_device}" +
            t('\n\nScript exécuté avec les droits administrateur :\n  ') + script +
            t('\n\n⚠  TOUTES LES DONNÉES SERONT EFFACÉES !\n\nContinuer ?'),
            danger=True,
        )
        if answer:
            self._run_install()

    def _run_install(self, force: bool = False):
        self.btn_install.configure(state="disabled", text=t('Installation...'))
        self._append_log(t('Démarrage de l\'installation Ventoy...\n'))

        def run():
            from core.ventoy_installer import (
                find_ventoy_in_dir, install_ventoy, prepare_verified_ventoy
            )
            script = find_ventoy_in_dir(self._ventoy_dir)
            if not script:
                self.after(0, lambda: self.winfo_exists() and self._append_log(
                    t('Erreur : ventoy2disk.sh introuvable.')))
                self.after(0, lambda: self.winfo_exists() and self.btn_install.configure(
                    state="normal", text=t('💾  Installer Ventoy')))
                return

            # A copy from the download cache is re-verified against the
            # official checksum and run from a fresh temp dir, never as-is
            prepared = prepare_verified_ventoy(script)
            if not prepared:
                self.after(0, lambda: self.winfo_exists() and self._append_log(
                    t("Erreur : impossible de vérifier l'intégrité de la copie de Ventoy "
                      "en cache (hors ligne ou archive modifiée). Retéléchargez Ventoy.\n")))
                self.after(0, lambda: self.winfo_exists() and self.btn_install.configure(
                    state="normal", text=t('💾  Installer Ventoy')))
                return
            script, tmp_dir = prepared

            def _on_output(txt):
                self.after(0, lambda t=txt: self.winfo_exists() and self._append_log(t))

            try:
                success, output = install_ventoy(
                    device=self._selected_device,
                    ventoy_script=script,
                    force=force,
                    on_output=_on_output,
                )
            finally:
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
            self.after(0, lambda s=success, o=output:
                       self.winfo_exists() and self._on_install_done(s, o))

        threading.Thread(target=run, daemon=True).start()

    def _on_install_done(self, success: bool, output: str):
        self._append_log(output)
        self.btn_install.configure(state="normal", text=t('💾  Installer Ventoy'))
        if success:
            show_info(
                self, t('Succès'),
                t("✓ Ventoy installé avec succès !\n\nRetirez et rebranchez la clé USB puis actualisez VentoyIsoUpdater."),
            )
            if self.on_done:
                self.on_done()
            self.destroy()
        elif "already contains a Ventoy" in output:
            # A plain -i install refuses outright once the disk isn't
            # blank — offer -I (force reinstall) instead of a dead end.
            if ask_yes_no(
                self, t('Ventoy déjà présent'),
                t("Ce disque contient déjà une installation de Ventoy.\n\n"
                  "Voulez-vous forcer la réinstallation ?\n"
                  "⚠  TOUTES LES DONNÉES SERONT EFFACÉES !"),
                danger=True,
            ):
                self._run_install(force=True)
        else:
            show_error(
                self, t('Erreur'),
                t('L\'installation a échoué.\nConsultez la sortie ci-dessous.'),
            )

    def _append_log(self, text: str):
        current = self.lbl_log.cget("text")
        self.lbl_log.configure(text=current + text)

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.lbl_log.cget("text"))
        self.btn_copy_log.configure(text=t('✓  Copié'))
        self.after(1500, lambda: self.winfo_exists() and
                   self.btn_copy_log.configure(text=t('📋  Copier')))


# ══════════════════════════════════════════════════════════════════════════════
#  Dialog : Journal de synchronisation des logos
# ══════════════════════════════════════════════════════════════════════════════

class SyncLogDialog(ctk.CTkToplevel):
    """Shows the detailed result of the logo sync."""

    def __init__(self, parent, results: dict):
        super().__init__(parent)
        self.title(t('Journal de synchronisation des logos'))
        self.geometry("560x420")
        self.resizable(True, True)
        self.after(100, self.lift)
        self.after(150, self.focus_force)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        ok   = sum(1 for v in results.values() if v["success"])
        fail = sum(1 for v in results.values() if not v["success"])
        dl   = sum(1 for v in results.values() if not v["skipped"] and v["success"])

        summary_color = "#2ecc71" if not fail else "#e74c3c"
        summary_text = f"{ok}{t(' logo(s) OK')}"
        if dl:
            summary_text += f"  ({dl}{t(' téléchargé(s))')}"
        if fail:
            summary_text += f"  •  {fail}{t(' échec(s)')}"

        ctk.CTkLabel(
            self, text=summary_text,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=summary_color
        ).grid(row=0, column=0, padx=16, pady=(14, 6), sticky="w")

        # Liste
        scroll = ctk.CTkScrollableFrame(self)
        scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        scroll.grid_columnconfigure(1, weight=1)

        # Sort: failures first, then downloaded successes, then skipped
        def sort_key(item):
            v = item[1]
            if not v["success"]:
                return 0
            if not v["skipped"]:
                return 1
            return 2

        for i, (filename, info) in enumerate(sorted(results.items(), key=sort_key)):
            bg = "#2b2b2b" if i % 2 == 0 else "#252525"
            row = ctk.CTkFrame(scroll, fg_color=bg, corner_radius=4)
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            row.grid_columnconfigure(1, weight=1)

            # Status icon
            if info["success"] and not info["skipped"]:
                icon, color = "⬇", "#2ecc71"
            elif info["success"]:
                icon, color = "✓", "#27ae60"
            else:
                icon, color = "✗", "#e74c3c"

            ctk.CTkLabel(row, text=icon, font=ctk.CTkFont(size=13),
                         text_color=color, width=24
                         ).grid(row=0, column=0, padx=(8, 4), pady=6)

            # Nom distro + fichier
            name_text = info["name"]
            ctk.CTkLabel(row, text=name_text, font=ctk.CTkFont(size=12),
                         anchor="w"
                         ).grid(row=0, column=1, padx=4, pady=6, sticky="w")

            ctk.CTkLabel(row, text=filename, font=ctk.CTkFont(size=10),
                         text_color="gray60", anchor="w"
                         ).grid(row=0, column=2, padx=8, pady=6, sticky="w")

            # Failure reason
            if info["error"]:
                ctk.CTkLabel(row, text=info["error"],
                             font=ctk.CTkFont(size=10), text_color="#e67e22",
                             anchor="w", wraplength=200
                             ).grid(row=1, column=1, columnspan=2, padx=4, pady=(0, 4), sticky="w")

        ctk.CTkButton(self, text=t('Fermer'), command=self.destroy, width=100
                      ).grid(row=2, column=0, pady=(6, 12))


# ══════════════════════════════════════════════════════════════════════════════
#  Dialog: full Ventoy theme editor
# ══════════════════════════════════════════════════════════════════════════════

class ThemeEditorDialog(ctk.CTkToplevel):
    """
    Full editor for the Ventoy theme on the USB drive.
    3 tabs:
      1. theme.txt  — text editor with automatic save + backup
      2. Images     — decorative theme PNGs (background, buttons, etc.)
      3. Icons      — distro logos (icons/ folder)
    """

    _THUMB_SIZE  = (64, 64)    # miniatures pour les icônes distros
    _IMG_PREVIEW = (96, 96)    # aperçu pour les images du thème

    def __init__(self, parent, drive, distros_db: dict,
                 iso_entries: list = None,
                 on_change: Optional[Callable] = None):
        super().__init__(parent)

        theme_name = os.path.basename(drive.theme_dir) if drive.theme_dir else t('thème')
        self.title(f"{t('Éditeur de thème — ')}{theme_name}")
        self.geometry("960x660")
        self.resizable(True, True)
        self.after(100, self.lift)
        self.after(150, self.focus_force)

        self._drive       = drive
        self._theme_dir   = drive.theme_dir
        self._icons_dir   = drive.theme_icons_dir
        self._on_change   = on_change
        self._iso_entries = iso_entries or []
        self._distros_db  = distros_db

        # Caches pour les miniatures
        self._icon_thumb_cache: dict[str, object] = {}
        self._img_thumb_cache: dict[str, object]  = {}

        # Search variable for the Icons tab
        self._icon_search_var = ctk.StringVar()
        self._icon_search_var.trace_add("write", lambda *_: self._icons_refresh())

        self._build()

    # ─────────────────────────── SCROLL HELPER ──────────────────────────────

    def _bind_scroll_to(self, widget, canvas):
        """Recursively forwards the mouse wheel from all children to the given canvas."""
        # One "unit" per event regardless of e.delta's magnitude — see
        # _bind_table_scroll's comment.
        widget.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"), add="+")
        widget.bind("<Button-4>",
                    lambda e: canvas.yview_scroll(-1, "units"), add="+")
        widget.bind("<Button-5>",
                    lambda e: canvas.yview_scroll(1, "units"), add="+")
        for child in widget.winfo_children():
            self._bind_scroll_to(child, canvas)

    # ─────────────────────────── BUILD ──────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        tabs = ctk.CTkTabview(self)
        tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))

        tabs.add(t('📝  theme.txt'))
        tabs.add(t('🖼  Images'))
        tabs.add(t('🎨  Icônes distros'))
        tabs.add(t('🖥  Aperçu'))

        self._build_txt_tab(tabs.tab(t('📝  theme.txt')))
        self._build_img_tab(tabs.tab(t('🖼  Images')))
        self._build_icons_tab(tabs.tab(t('🎨  Icônes distros')))
        self._build_preview_tab(tabs.tab(t('🖥  Aperçu')))

        ctk.CTkButton(self, text=t('Fermer'), command=self.destroy, width=100
                      ).grid(row=1, column=0, pady=(6, 10), sticky="e", padx=16)

    # ══════════════════════ ONGLET 1 — theme.txt ════════════════════════════

    def _build_txt_tab(self, frame: ctk.CTkFrame):
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # ── Barre d'outils ──────────────────────────────────────────────────
        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        bar.grid_columnconfigure(3, weight=1)

        ctk.CTkButton(
            bar, text=t('💾  Sauvegarder'), width=130,
            fg_color="#1a5276", hover_color="#154360",
            command=self._txt_save
        ).grid(row=0, column=0, padx=4)

        ctk.CTkButton(
            bar, text=t('↺  Recharger'), width=110,
            fg_color="gray35", hover_color="gray25",
            command=self._txt_reload
        ).grid(row=0, column=1, padx=4)

        self._txt_status = ctk.CTkLabel(
            bar, text="", font=ctk.CTkFont(size=11), text_color="gray55"
        )
        self._txt_status.grid(row=0, column=3, sticky="e", padx=8)

        # Chemin du fichier
        txt_path = self._theme_txt_path()
        ctk.CTkLabel(
            frame, text=f"{t('Fichier : ')}{txt_path or t('(theme.txt introuvable)')}",
            font=ctk.CTkFont(size=10), text_color="gray50", anchor="w"
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 2))

        # ── Text editor ────────────────────────────────────────────────
        self._txt_box = ctk.CTkTextbox(
            frame, font=ctk.CTkFont(family="Monospace", size=12),
            wrap="none", activate_scrollbars=True
        )
        self._txt_box.grid(row=2, column=0, sticky="nsew", padx=6, pady=4)
        frame.grid_rowconfigure(2, weight=1)

        self._txt_reload()

    def _theme_txt_path(self) -> Optional[str]:
        if not self._theme_dir:
            return None
        p = os.path.join(self._theme_dir, "theme.txt")
        return p if os.path.isfile(p) else None

    def _txt_reload(self):
        path = self._theme_txt_path()
        self._txt_box.delete("0.0", "end")
        if path:
            try:
                content = open(path, encoding="utf-8", errors="replace").read()
                self._txt_box.insert("0.0", content)
                self._txt_status.configure(text=t('Chargé.'), text_color="gray55")
            except Exception as e:
                self._txt_box.insert("0.0", f"{t('# Erreur de lecture : ')}{e}")
                self._txt_status.configure(text=str(e), text_color="#e74c3c")
        else:
            self._txt_box.insert("0.0", t('# theme.txt introuvable dans ce dossier.'))
            self._txt_status.configure(text=t('Fichier absent.'), text_color="#f39c12")

    def _txt_save(self):
        path = self._theme_txt_path()
        if not path:
            show_error(self, t('Erreur'), t('theme.txt introuvable — impossible de sauvegarder.'))
            return
        content = self._txt_box.get("0.0", "end")
        # Backup automatique
        backup = path + ".bak"
        try:
            if os.path.isfile(path):
                shutil.copy2(path, backup)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._txt_status.configure(
                text=f"{t('Sauvegardé  (backup : ')}{os.path.basename(backup)})",
                text_color="#2ecc71"
            )
            if self._on_change:
                self._on_change()
        except Exception as e:
            show_error(self, t('Erreur'), t(str(e)))
            self._txt_status.configure(text=f"{t('Échec : ')}{e}", text_color="#e74c3c")

    # ══════════════════════ TAB 2 — Theme images ══════════════════════

    def _build_img_tab(self, frame: ctk.CTkFrame):
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # ── Barre d'outils ──────────────────────────────────────────────────
        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        bar.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            bar, text=t('⬆  Importer un PNG'), width=150,
            fg_color="#1a5276", hover_color="#154360",
            command=self._img_import
        ).grid(row=0, column=0, padx=4)

        ctk.CTkLabel(
            bar,
            text=t('PNG du thème (fond d\'écran, boutons…) — hors dossier icons/'),
            font=ctk.CTkFont(size=11), text_color="gray55"
        ).grid(row=0, column=1, sticky="w", padx=8)

        # ── Grille ──────────────────────────────────────────────────────────
        self._img_scroll = ctk.CTkScrollableFrame(frame)
        self._img_scroll.grid(row=1, column=0, sticky="nsew", padx=6, pady=4)

        self._img_count_lbl = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=11),
                                            text_color="gray55")
        self._img_count_lbl.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 4))

        self._img_refresh()

    def _list_theme_pngs(self) -> list[str]:
        """PNGs at the theme folder's root (not in icons/)."""
        if not self._theme_dir or not os.path.isdir(self._theme_dir):
            return []
        try:
            icons_name = os.path.basename(self._icons_dir) if self._icons_dir else "icons"
            return sorted(
                f for f in os.listdir(self._theme_dir)
                if f.lower().endswith(".png")
                and os.path.isfile(os.path.join(self._theme_dir, f))
            )
        except Exception:
            return []

    def _get_img_thumb(self, filename: str) -> Optional[object]:
        path = os.path.join(self._theme_dir, filename)
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0
        key = f"{filename}_{mtime}"
        if key not in self._img_thumb_cache:
            try:
                from PIL import Image
                img = Image.open(path).convert("RGBA").resize(
                    self._IMG_PREVIEW, Image.LANCZOS)
                self._img_thumb_cache[key] = ctk.CTkImage(
                    light_image=img, dark_image=img, size=self._IMG_PREVIEW)
            except Exception:
                self._img_thumb_cache[key] = None
        return self._img_thumb_cache[key]

    def _img_refresh(self):
        self._img_thumb_cache.clear()
        for w in self._img_scroll.winfo_children():
            w.destroy()

        pngs = self._list_theme_pngs()
        self._img_count_lbl.configure(text=f"{len(pngs)}{t(' image(s) PNG')}")

        if not pngs:
            msg = t('Aucune image PNG trouvée à la racine du thème.') \
                  if self._theme_dir else t('Aucun thème détecté sur la clé.')
            ctk.CTkLabel(self._img_scroll, text=msg,
                         font=ctk.CTkFont(size=12), text_color="gray60"
                         ).grid(row=0, column=0, padx=20, pady=30)
            return

        COLS = 4
        for col in range(COLS):
            self._img_scroll.grid_columnconfigure(col, weight=1)

        for i, filename in enumerate(pngs):
            r, c = divmod(i, COLS)
            self._img_render_tile(r, c, filename)

    def _img_render_tile(self, row: int, col: int, filename: str):
        tile = ctk.CTkFrame(self._img_scroll, corner_radius=8, fg_color="#1e1e2e")
        tile.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
        tile.grid_columnconfigure(0, weight=1)

        thumb = self._get_img_thumb(filename)
        if thumb:
            ctk.CTkLabel(tile, image=thumb, text="",
                         width=self._IMG_PREVIEW[0]
                         ).grid(row=0, column=0, pady=(10, 4))
        else:
            ctk.CTkLabel(tile, text="?", font=ctk.CTkFont(size=24), text_color="gray50",
                         width=self._IMG_PREVIEW[0], height=self._IMG_PREVIEW[1]
                         ).grid(row=0, column=0, pady=(10, 4))

        short = filename if len(filename) <= 18 else filename[:15] + "…"
        ctk.CTkLabel(tile, text=short, font=ctk.CTkFont(size=10),
                     text_color="gray70", wraplength=120
                     ).grid(row=1, column=0, padx=4, pady=(0, 4))

        ctk.CTkButton(
            tile, text=t('Remplacer'), width=90, height=24,
            fg_color="#1a5276", hover_color="#154360",
            command=lambda f=filename: self._img_replace(f)
        ).grid(row=2, column=0, pady=(0, 8))

        self._bind_scroll_to(tile, self._img_scroll._parent_canvas)

    def _img_import(self):
        paths = filedialog.askopenfilenames(
            title=t('Importer des images PNG'),
            filetypes=[(t('Images PNG'), "*.png"), (t('Tous les fichiers'), "*.*")],
            parent=self
        )
        if not paths:
            return
        for src in paths:
            dest = os.path.join(self._theme_dir, os.path.basename(src))
            try:
                shutil.copy2(src, dest)
            except Exception as e:
                show_error(self, t('Erreur'), t(str(e)))
        self._img_refresh()
        if self._on_change:
            self._on_change()

    def _img_replace(self, filename: str):
        src = filedialog.askopenfilename(
            title=f"{t('Remplacer ')}{filename}",
            filetypes=[(t('Images PNG'), "*.png"), (t('Tous les fichiers'), "*.*")],
            parent=self
        )
        if not src:
            return
        dest = os.path.join(self._theme_dir, filename)
        backup = dest + ".bak"
        try:
            if os.path.isfile(dest):
                shutil.copy2(dest, backup)
            shutil.copy2(src, dest)
        except Exception as e:
            show_error(self, t('Erreur'), t(str(e)))
            return
        self._img_refresh()
        if self._on_change:
            self._on_change()

    # ══════════════════════ TAB 3 — Distro icons ═══════════════════════

    def _build_icons_tab(self, frame: ctk.CTkFrame):
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # ── Barre d'outils ──────────────────────────────────────────────────
        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        bar.grid_columnconfigure(3, weight=1)

        ctk.CTkButton(
            bar, text=t('⬆  Importer PNG(s)'), width=140,
            fg_color="#1a5276", hover_color="#154360",
            command=self._icons_import
        ).grid(row=0, column=0, padx=4)

        ctk.CTkButton(
            bar, text=t('🌐  Dépôt de logos'), width=140,
            fg_color="#1a4a1a", hover_color="#144014",
            command=self._icons_open_repo
        ).grid(row=0, column=1, padx=4)

        ctk.CTkEntry(
            bar, textvariable=self._icon_search_var,
            placeholder_text=t('🔍 Filtrer...'),
            font=ctk.CTkFont(size=12), width=160, height=30
        ).grid(row=0, column=3, padx=(8, 4), sticky="e")

        folder_text = self._icons_dir or t('(icons/ introuvable)')
        ctk.CTkLabel(
            bar, text=f"{t('Dossier : ')}{folder_text}",
            font=ctk.CTkFont(size=10), text_color="gray55"
        ).grid(row=1, column=0, columnspan=4, padx=4, pady=(2, 0), sticky="w")

        # ── Grille ──────────────────────────────────────────────────────────
        self._icons_scroll = ctk.CTkScrollableFrame(frame)
        self._icons_scroll.grid(row=1, column=0, sticky="nsew", padx=6, pady=4)

        self._icons_count_lbl = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=11),
                                              text_color="gray60")
        self._icons_count_lbl.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 4))

        self._icons_refresh()

    def _list_icon_pngs(self) -> list[str]:
        if not self._icons_dir or not os.path.isdir(self._icons_dir):
            return []
        try:
            return sorted(
                f for f in os.listdir(self._icons_dir) if f.lower().endswith(".png")
            )
        except Exception:
            return []

    def _get_icon_thumb(self, filename: str) -> Optional[object]:
        path = os.path.join(self._icons_dir, filename)
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0
        key = f"{filename}_{mtime}"
        if key not in self._icon_thumb_cache:
            try:
                from PIL import Image
                img = Image.open(path).convert("RGBA").resize(
                    self._THUMB_SIZE, Image.LANCZOS)
                self._icon_thumb_cache[key] = ctk.CTkImage(
                    light_image=img, dark_image=img, size=self._THUMB_SIZE)
            except Exception:
                self._icon_thumb_cache[key] = None
        return self._icon_thumb_cache[key]

    def _icons_refresh(self):
        query = self._icon_search_var.get().strip().lower()
        self._icon_thumb_cache.clear()
        for w in self._icons_scroll.winfo_children():
            w.destroy()

        pngs = [f for f in self._list_icon_pngs() if not query or query in f.lower()]
        self._icons_count_lbl.configure(text=f"{len(pngs)}{t(' icône(s) PNG')}")

        if not pngs:
            msg = t('Aucun logo PNG trouvé.\nUtilisez \'Importer PNG(s)\' pour en ajouter.') \
                  if self._icons_dir else t('Dossier icons/ introuvable sur la clé.')
            ctk.CTkLabel(self._icons_scroll, text=msg,
                         font=ctk.CTkFont(size=12), text_color="gray60"
                         ).grid(row=0, column=0, padx=20, pady=30)
            return

        COLS = 5
        for col in range(COLS):
            self._icons_scroll.grid_columnconfigure(col, weight=1)

        for i, filename in enumerate(pngs):
            r, c = divmod(i, COLS)
            self._icons_render_tile(r, c, filename)

    def _icons_render_tile(self, row: int, col: int, filename: str):
        tile = ctk.CTkFrame(self._icons_scroll, corner_radius=8, fg_color="#1e1e2e")
        tile.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
        tile.grid_columnconfigure(0, weight=1)

        thumb = self._get_icon_thumb(filename)
        if thumb:
            ctk.CTkLabel(tile, image=thumb, text="",
                         width=self._THUMB_SIZE[0]
                         ).grid(row=0, column=0, pady=(10, 4))
        else:
            ctk.CTkLabel(tile, text="?", font=ctk.CTkFont(size=24), text_color="gray50",
                         width=self._THUMB_SIZE[0], height=self._THUMB_SIZE[1]
                         ).grid(row=0, column=0, pady=(10, 4))

        name = filename[:-4] if filename.lower().endswith(".png") else filename
        ctk.CTkLabel(tile, text=name, font=ctk.CTkFont(size=10),
                     text_color="gray70", wraplength=110
                     ).grid(row=1, column=0, padx=4, pady=(0, 6))

        ctk.CTkButton(
            tile, text="🗑", width=32, height=24,
            fg_color="#7f1c1c", hover_color="#a93226",
            command=lambda f=filename: self._icons_delete(f)
        ).grid(row=2, column=0, pady=(0, 8))

        self._bind_scroll_to(tile, self._icons_scroll._parent_canvas)

    def _icons_import(self):
        if not self._icons_dir:
            show_error(self, t('Erreur'), t('Dossier icons/ introuvable.'))
            return
        paths = filedialog.askopenfilenames(
            title=t('Importer des logos PNG'),
            filetypes=[(t('Images PNG'), "*.png"), (t('Tous les fichiers'), "*.*")],
            parent=self
        )
        if not paths:
            return
        imported, errors = 0, []
        for src in paths:
            dest = os.path.join(self._icons_dir, os.path.basename(src))
            try:
                from PIL import Image
                img = Image.open(src).convert("RGBA").resize((128, 128), Image.LANCZOS)
                img.save(dest, "PNG")
                imported += 1
            except Exception:
                try:
                    shutil.copy2(src, dest)
                    imported += 1
                except Exception as e2:
                    errors.append(f"{os.path.basename(src)}: {e2}")
        if errors:
            show_warning(
                self, t('Avertissement'),
                f"{imported}{t(' logo(s) importé(s).\nÉchecs :\n')}" + "\n".join(errors),
            )
        self._icons_refresh()
        _logo_cache.clear()
        if self._on_change:
            self._on_change()

    def _icons_delete(self, filename: str):
        if not ask_yes_no(
            self, t('Supprimer'), f"{t('Supprimer définitivement :\n')}{filename} ?",
            danger=True,
        ):
            return
        path = os.path.join(self._icons_dir, filename)
        try:
            os.remove(path)
        except Exception as e:
            show_error(self, t('Erreur'), t(str(e)))
            return
        self._icons_refresh()
        _logo_cache.clear()
        if self._on_change:
            self._on_change()

    def _icons_open_repo(self):
        if not self._icons_dir:
            show_error(self, t('Erreur'), t('Dossier icons/ introuvable.'))
            return
        LogoRepoBrowserDialog(
            self, self._icons_dir,
            on_import=lambda: (self._icons_refresh(),
                               _logo_cache.clear(),
                               self._on_change() if self._on_change else None)
        )

    # ══════════════════════ TAB 4 — Theme preview ══════════════════════

    def _build_preview_tab(self, frame: ctk.CTkFrame):
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        bar.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            bar, text=t('🔄  Actualiser l\'aperçu'), width=170,
            fg_color="#1a5276", hover_color="#154360",
            command=lambda: threading.Thread(
                target=self._preview_build, daemon=True).start()
        ).grid(row=0, column=0, padx=4)

        self._preview_status_lbl = ctk.CTkLabel(
            bar, text=t('Rendu en cours…'), font=ctk.CTkFont(size=11), text_color="gray55"
        )
        self._preview_status_lbl.grid(row=0, column=1, sticky="w", padx=8)

        canvas_wrap = ctk.CTkFrame(frame, fg_color="#0a0a0a", corner_radius=8)
        canvas_wrap.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        canvas_wrap.grid_columnconfigure(0, weight=1)
        canvas_wrap.grid_rowconfigure(0, weight=1)

        self._preview_canvas = tk.Canvas(
            canvas_wrap, bg="#000000", highlightthickness=0,
            width=854, height=480
        )
        self._preview_canvas.grid(row=0, column=0, padx=4, pady=4)
        canvas_wrap.bind("<Configure>", self._on_preview_resize)
        self._preview_photo = None   # référence PhotoImage anti-GC
        self._preview_resize_job = None

        threading.Thread(target=self._preview_build, daemon=True).start()

    def _on_preview_resize(self, event):
        """Debounces resizing so the render isn't retriggered on every pixel."""
        if self._preview_resize_job:
            try:
                self.after_cancel(self._preview_resize_job)
            except Exception:
                pass
        w = max(event.width - 8, 160)
        h = max(event.height - 8, 90)
        if w / h > 16 / 9:
            w = int(h * 16 / 9)
        else:
            h = int(w * 9 / 16)
        self._preview_canvas.config(width=w, height=h)
        self._preview_resize_job = self.after(
            400, lambda: threading.Thread(
                target=self._preview_build, daemon=True).start()
        )

    # ── Parser theme.txt ────────────────────────────────────────────────────

    def _parse_theme_full(self) -> tuple[dict, list[dict]]:
        """
        Full parse of the GRUB2 theme.txt format.
        Returns (global_props, [components]).
        Each component is a dict {"type": str, ...props}.
        Handles the ':' (global) and '=' (inside blocks) separators.
        """
        gprops: dict = {
            "desktop-color": "#000000",
            "desktop-image": None,
            "title-text":    "",
            "title-color":   "#ffffff",
        }
        components: list[dict] = []

        path = self._theme_txt_path()
        if not path:
            return gprops, components

        try:
            lines = open(path, encoding="utf-8", errors="replace").readlines()
        except Exception:
            return gprops, components

        import re as _re
        current: Optional[dict] = None

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            # Start of a component: + type_name {  (or + type_name name {)
            if line.startswith("+"):
                parts = line.split()
                ctype = parts[1] if len(parts) > 1 else "unknown"
                current = {"type": ctype}
                components.append(current)
                continue

            if line == "}":
                current = None
                continue

            # Property: accepts 'key: val', 'key = val', 'key: "val"'
            m = _re.match(r'^([\w\-]+)\s*[=:]\s*"?([^"]*)"?\s*;?$', line)
            if not m:
                continue
            key = m.group(1).strip()
            val = m.group(2).strip()

            if current is not None:
                current[key] = val
            else:
                gprops[key] = val

        return gprops, components

    def _resolve(self, val: str, total: int, default: int = 0) -> int:
        """
        Resolves a GRUB2 coordinate into pixels:
          '15%'        -> 15% of total
          'c'          -> total // 2
          'c+50'       -> total // 2 + 50
          '100%-200'   -> total - 200
          '320'        -> 320
        """
        import re as _re
        s = str(val).strip()
        if not s:
            return default
        # c±n
        m = _re.match(r'^c\s*([+-]\s*\d+)?$', s)
        if m:
            return total // 2 + (int(m.group(1).replace(" ", "")) if m.group(1) else 0)
        # XX%±n
        m = _re.match(r'^(\d+)%\s*([+-]\s*\d+)?$', s)
        if m:
            base = int(total * int(m.group(1)) / 100)
            off  = int(m.group(2).replace(" ", "")) if m.group(2) else 0
            return base + off
        try:
            return int(s)
        except ValueError:
            return default

    # ── Rendu PIL en thread ──────────────────────────────────────────────────

    def _preview_build(self):
        """Builds the theme's PIL image, then displays it on the canvas (bg thread)."""
        if not self.winfo_exists():
            return
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk
        except ImportError:
            self.after(0, lambda: self.winfo_exists() and
                       self._preview_status_lbl.configure(
                           text=t('PIL/Pillow requis pour l\'aperçu.')))
            return

        c  = self._preview_canvas
        cw = c.winfo_width()  or 854
        ch = c.winfo_height() or 480

        gprops, components = self._parse_theme_full()

        # ── Couleur de fond de base ──────────────────────────────────────────
        bg_hex = gprops.get("desktop-color", "#000000") or "#000000"
        try:
            base = Image.new("RGBA", (cw, ch), bg_hex)
        except Exception:
            base = Image.new("RGBA", (cw, ch), "#000000")

        pngs_used: list[str] = []

        # ── Helper : charge et colle un PNG avec alpha ───────────────────────
        def paste_png(path: str, x: int, y: int, w: int, h: int):
            if not os.path.isfile(path):
                return
            try:
                img = Image.open(path).convert("RGBA")
                if w > 0 and h > 0:
                    img = img.resize((w, h), Image.LANCZOS)
                base.alpha_composite(img, dest=(max(0, x), max(0, y)))
                pngs_used.append(os.path.basename(path))
            except Exception:
                pass

        # ── desktop-image (fond) ─────────────────────────────────────────────
        bg_file = gprops.get("desktop-image")
        if bg_file and self._theme_dir:
            paste_png(os.path.join(self._theme_dir, bg_file), 0, 0, cw, ch)

        # ── Tous les composants du theme.txt dans l'ordre ────────────────────
        boot_menu_comp: Optional[dict] = None

        for comp in components:
            ctype = comp.get("type", "")

            if ctype == "image":
                # + image { left=X top=Y width=W height=H file="foo.png" }
                file_ = comp.get("file", "")
                if file_ and self._theme_dir:
                    x = self._resolve(comp.get("left",   "0"), cw)
                    y = self._resolve(comp.get("top",    "0"), ch)
                    w = self._resolve(comp.get("width",  "0"), cw)
                    h = self._resolve(comp.get("height", "0"), ch)
                    paste_png(os.path.join(self._theme_dir, file_), x, y, w, h)

            elif ctype == "boot_menu":
                boot_menu_comp = comp   # rendered after the other layers

            elif ctype == "label":
                # Overlaid text
                text  = comp.get("text", "").strip('"')
                if not text:
                    continue
                color = comp.get("color", "#ffffff")
                x = self._resolve(comp.get("left",   "0"), cw)
                y = self._resolve(comp.get("top",    "0"), ch)
                try:
                    draw = ImageDraw.Draw(base)
                    draw.text((x, y), text, fill=color)
                except Exception:
                    pass

        # ── Rendering the boot_menu area (on top of everything) ─────────────────────
        if boot_menu_comp:
            bm = boot_menu_comp
            mx = self._resolve(bm.get("left",   "15%"), cw)
            my = self._resolve(bm.get("top",    "20%"), ch)
            mw = self._resolve(bm.get("width",  "70%"), cw)
            mh = self._resolve(bm.get("height", "60%"), ch)

            item_h  = max(int(bm.get("item_height",  "42") or 42), 8)
            padding = max(int(bm.get("item_padding", "14") or 14), 0)
            icon_w  = max(int(bm.get("icon_width",   "32") or 32), 4)
            icon_h  = max(int(bm.get("icon_height",  "32") or 32), 4)
            item_color   = bm.get("item_color",          "#cccccc")
            sel_color    = bm.get("selected_item_color", "#ffffff")

            # Selection highlight: item_pixmap_style="select_*.png"
            sel_pixmap = bm.get("item_pixmap_style", "")
            sel_png_path: Optional[str] = None
            if sel_pixmap and self._theme_dir:
                # Looks for the "select_c.png" file or the _c variant
                import re as _re, glob as _glob
                pattern = sel_pixmap.replace("*", "*")
                candidates = _glob.glob(
                    os.path.join(self._theme_dir, sel_pixmap.replace("*", "*")))
                # Prefers _c (center) to fill the entire width
                for suffix in ("_c", "c", ""):
                    pat = sel_pixmap.replace("*", suffix)
                    p = os.path.join(self._theme_dir, pat)
                    if os.path.isfile(p):
                        sel_png_path = p
                        break
                if not sel_png_path and candidates:
                    sel_png_path = candidates[0]

            # Menu background area (semi-transparent)
            overlay = Image.new("RGBA", (mw, mh), (0, 0, 0, 120))
            base.alpha_composite(overlay, dest=(mx, my))

            # Entries
            entries = self._get_preview_entries()
            max_visible = max(1, mh // item_h)
            draw = ImageDraw.Draw(base)

            for idx, (label, grub_class) in enumerate(entries[:max_visible]):
                iy = my + idx * item_h
                selected = (idx == 0)

                # Selection background
                if selected:
                    if sel_png_path and os.path.isfile(sel_png_path):
                        paste_png(sel_png_path, mx, iy, mw, item_h)
                    else:
                        sel_overlay = Image.new("RGBA", (mw, item_h), (26, 80, 130, 200))
                        base.alpha_composite(sel_overlay, dest=(mx, iy))

                # Distro icon
                icon_x_end = padding
                if grub_class and self._icons_dir:
                    ic_path = os.path.join(self._icons_dir, grub_class + ".png")
                    if os.path.isfile(ic_path):
                        try:
                            ic = Image.open(ic_path).convert("RGBA").resize(
                                (icon_w, icon_h), Image.LANCZOS)
                            iy_center = iy + (item_h - icon_h) // 2
                            base.alpha_composite(
                                ic, dest=(mx + padding, max(0, iy_center)))
                            icon_x_end = padding + icon_w + 6
                            pngs_used.append(os.path.basename(ic_path))
                        except Exception:
                            pass

                # Entry text (filename only, to avoid clutter)
                text_x = mx + icon_x_end + 4
                text_y = iy + item_h // 2 - 7
                try:
                    draw.text(
                        (text_x, text_y),
                        label,
                        fill=sel_color if selected else item_color
                    )
                except Exception:
                    pass

        # ── Affichage sur canvas ─────────────────────────────────────────────
        final = base.convert("RGB")
        photo = ImageTk.PhotoImage(final)

        def show():
            if not self.winfo_exists():
                return
            self._preview_photo = photo
            self._preview_canvas.config(bg="#000000")
            self._preview_canvas.delete("all")
            self._preview_canvas.create_image(0, 0, anchor="nw", image=photo)
            unique_pngs = sorted(set(pngs_used))
            self._preview_status_lbl.configure(
                text=f"{len(unique_pngs)}{t(' PNG composé(s) · ')}{cw}×{ch}px"
                     + (f" — {', '.join(unique_pngs[:6])}"
                        + ("…" if len(unique_pngs) > 6 else "")
                        if unique_pngs else t(' — aucun PNG trouvé')),
                text_color="gray55"
            )

        self.after(0, show)

    def _get_preview_entries(self) -> list[tuple[str, Optional[str]]]:
        """Menu entries: known ISOs on the drive, or a demo if empty."""
        entries = []
        seen = set()
        for iso in self._iso_entries:
            if iso.distro_id and iso.distro_id not in seen:
                cfg = next((d for d in self._distros_db.get("distros", [])
                            if d["id"] == iso.distro_id), None)
                if cfg:
                    entries.append((cfg.get("name", iso.filename),
                                    cfg.get("grub_class")))
                    seen.add(iso.distro_id)
            elif not iso.distro_id:
                entries.append((iso.filename, None))
        if not entries:
            entries = [
                (t('Ubuntu 24.04 LTS'),     "ubuntu"),
                (t('Fedora 42 Workstation'), "fedora"),
                (t('Debian 12'),             "debian"),
                (t('Arch Linux'),            "arch"),
                (t('Windows 11'),            "redmond"),
                (t('System Rescue'),         "systemrescue"),
            ]
        return entries


#  Dialog: browser for the lutgaru/linux-distro-logos repository
# ══════════════════════════════════════════════════════════════════════════════

# Full list of logos available in the repository (729 files)
_LUTGARU_FILES = [
    "2x.png","64studio.png","absolute.png","abuledu.png","adamantix.png","adios.png",
    "admelix.png","agilia.png","alamlug.png","aleader.png","alinex.png","alinux.png",
    "alixe.png","alpine.png","alt.png","amaroklive.png","amber.png","ankur.png",
    "annvix.png","annyung.png","anonymos.png","antemium.png","antix.png","antomic.png",
    "apodio.png","aptosid.png","arabbix.png","arabian.png","arch.png","archbang.png",
    "archeos.png","archie.png","ares.png","arios.png","ark.png","artistx.png",
    "arudius.png","asianlinux.png","asianux.png","aslinux.png","asp.png","astaro.png",
    "asterisknow.png","asturix.png","athene.png","atmission.png","atomix.png",
    "auditor.png","augustux.png","aurora.png","auroraos.png","aurox.png","austrumi.png",
    "avlinux.png","ayrsoft.png","b2d.png","backtrack.png","baltix.png","bardinux.png",
    "bayanihan.png","beafanatix.png","bearops.png","beatrix.png","bee.png","beehive.png",
    "beernix.png","belenix.png","berry.png","best.png","biadix.png","biglinux.png",
    "bintoo.png","biobrew.png","bioknoppix.png","blackpanther.png","blackrhino.png",
    "blag.png","blankon.png","blin.png","blue.png","bluepoint.png","bluewall.png",
    "bluewhite64.png","bodhi.png","bonzai.png","boss.png","boten.png","brlix.png",
    "brlspeak.png","bsdanywhere.png","buffalo.png","bulinux.png","burapha.png","byo.png",
    "byzantineos.png","cae.png","caine.png","caixamagica.png","calculate.png","caldera.png",
    "canaima.png","caos.png","catix.png","ccux.png","cdlinux.png","censornet.png",
    "centos.png","chakra.png","chinese2000.png","chinese20001.png","clarkconnect.png",
    "cle.png","clearos.png","clonezilla.png","clusterix.png","clusterknoppix.png",
    "cobind.png","college.png","comfusion.png","condorux.png","conectiva.png",
    "connochaet.png","cool.png","core.png","corel.png","cosix.png","coyote.png",
    "cpubuilders.png","crunchbang.png","crux.png","ctkarch.png","damnsmall.png",
    "danix.png","darkstar.png","deadcd.png","debian.png","debris.png","debxpde.png",
    "decp.png","deepin.png","deepwater.png","defender.png","definity.png","deft.png",
    "deli.png","demolinux.png","demudi.png","desktopbsd.png","devil.png","digantel.png",
    "dizinha.png","dnalinux.png","doudou.png","draco.png","dragonflybsd.png","dragora.png",
    "dreamlinux.png","drinou.png","dvl.png","dw-weekly.png","dynasoft.png","dynebolic.png",
    "dzongkha.png","eadem.png","eagle.png","earos.png","easypeasy.png","easys.png",
    "edubuntu.png","eduknoppix.png","edulinux.png","ehad.png","ekaaty.png","elastix.png",
    "elearnix.png","element.png","elementary.png","elive.png","elpicx.png","elx.png",
    "endian.png","engarde.png","epidemic.png","eridani.png","erposs.png","esmith.png",
    "estrellaroja.png","esun.png","esware.png","euronode.png","evilentity.png","evinux.png",
    "extix.png","ezplanet.png","famelix.png","faunos.png","feather.png","featherweight.png",
    "fedora.png","fermi.png","finnix.png","fire.png","firefly.png","flash.png","flonix.png",
    "fluxbuntu.png","foresight.png","fork.png","fox.png","freebsd.png","freedows.png",
    "freeduc.png","freeducsup.png","freenas.png","freepia.png","freesbie.png",
    "freespire.png","frenzy.png","frugalware.png","ftosx.png","fuduntu.png","fuguita.png",
    "funtoo.png","fusion.png","geexbox.png","gelecek.png","genieos.png","gentoo.png",
    "gentooth.png","gentoox.png","geolivre.png","ghostbsd.png","gibraltar.png","ging.png",
    "gnacktrack.png","gnewsense.png","gnix.png","gnobsd.png","gnoppix.png","gnox.png",
    "gnustep.png","goblinx.png","gobo.png","gos.png","gparted.png","grafpup.png",
    "granular.png","greenie.png","grml.png","guadalinex.png","gulicbsd.png","h3knix.png",
    "haansoft.png","hacao.png","hakin9.png","hancom.png","happy.png","happymac.png",
    "haydar.png","hedinux.png","helix.png","heretix.png","hikarunix.png","hispafuentes.png",
    "hiweed.png","hklpg.png","holon.png","honeywall.png","howtux.png","hpsecure.png",
    "hymera.png","ibox.png","icepack.png","ichthux.png","idms.png","igelle.png",
    "ignalum.png","imagicos.png","imagineos.png","immunix.png","impi.png","incognito.png",
    "indlinux.png","inquisitor.png","insert.png","insigne.png","ipcop.png","ipfire.png",
    "jacklab.png","jamd.png","jblinux.png","jibbed.png","jolinux.png","jolios.png",
    "jollix.png","julex.png","jusix.png","k12linux.png","k12ltsp.png","kaella.png",
    "kahelos.png","kalango.png","kanotix.png","karamad.png","karoshi.png","kate.png",
    "kdemar.png","kinneret.png","kiwi.png","klax.png","klikit.png","klustrix.png",
    "kmlinux.png","knopils.png","knoppel.png","knopperdisk.png","knoppix.png",
    "knoppix64.png","knoppixmame.png","knoppixstd.png","knoppmyth.png","knosciences.png",
    "komodo.png","kondara.png","kongoni.png","kore.png","kororaa.png","krud.png",
    "kubuntu.png","kuki.png","kurumin.png","kwort.png","lamppix.png","las.png","laser5.png",
    "legacy.png","lfs.png","lg3d.png","lgis.png","libranet.png","liis.png","linare.png",
    "lindows.png","lineox.png","linespa.png","linex.png","linguasos.png","linhes.png",
    "linnexos.png","linpus.png","linspire.png","linuxconsole.png","linuxeducd.png",
    "linuxgamers.png","linuxin.png","linuxinstall.png","linuxo.png","linuxplus.png",
    "linuxppc.png","linuxtle.png","linuxxp.png","litrix.png","livecdrouter.png","livux.png",
    "llgp.png","lliurex.png","lnxbbc.png","loco.png","lonix.png","lorma.png","lrs.png",
    "lubuntu.png","luinux.png","luit.png","luminux.png","lunar.png","lycoris.png",
    "macpup.png","madbox.png","madeinlinux.png","mageia.png","magic.png","mandows.png",
    "mandrake.png","mandriva.png","mangaka.png","maryan.png","masonux.png","max.png",
    "mayix.png","mcnlive.png","medialab.png","medialinux.png","meego.png","mepis.png",
    "merdeka.png","midnightbsd.png","midori.png","miko.png","milax.png","minikazit.png",
    "minino.png","minislack.png","minix.png","mint.png","miracle.png","miros.png",
    "mizi.png","moblin.png","mockup.png","molinux.png","momonga.png","monomaxos.png",
    "monoppix.png","monowall.png","moonos.png","morphix.png","movix.png","msc.png",
    "mumi.png","munjoy.png","muriqui.png","murix.png","musix.png","mutagenix.png",
    "myah.png","mylinux.png","myrinix.png","mythbuntu.png","mythdora.png","nasgaia.png",
    "natures.png","navaho.png","navynos.png","neat.png","neoshine.png","nepalinux.png",
    "netbsd.png","netrunner.png","netsecl.png","netwosix.png","nexenta.png",
    "nexentastor.png","niigata.png","nimblex.png","nitix.png","nix.png","nonux.png",
    "nordisknoppix.png","nova.png","novell.png","nst.png","nubuntu.png","nutyx.png",
    "nuxone.png","octoz.png","oeone.png","ogoknoppix.png","ojuba.png","olive.png",
    "olivebsd.png","olpc.png","omoikane.png","onebase.png","onet.png","openbsd.png",
    "opendesktop.png","openfiler.png","opengeu.png","openindiana.png","openlab.png",
    "openlx.png","openmamba.png","openna.png","opensls.png","opensolaris.png",
    "openwall.png","ophcrack.png","oracle.png","oralux.png","other.png","overclockix.png",
    "ozos.png","paipix.png","paldo.png","papug.png","parallelknoppix.png","pardus.png",
    "parsix.png","parslinux.png","partedmagic.png","pcbsd.png","pclinuxos.png","pcos.png",
    "peachtree.png","peanut.png","pelicanhpc.png","penguinsleuth.png","pentoo.png",
    "peppermint.png","pequelin.png","pfsense.png","phaeronix.png","phat.png","phayoune.png",
    "phlak.png","phpsol.png","piebox.png","pilot.png","pingo.png","pinguy.png",
    "pingwinek.png","pioneer.png","plamo.png","planb.png","pld.png","plop.png",
    "polarbear.png","porteus.png","poseidon.png","pqui.png","privatix.png","progeny.png",
    "progex.png","projectdev.png","protech.png","puppy.png","puredyne.png","pureos.png",
    "qilinux.png","qimo.png","qomo.png","quantian.png","quirky.png","railslive.png",
    "rays.png","redflag.png","redhat.png","redmond.png","redoffice.png","redwall.png",
    "resala.png","resulinux.png","rip.png","rock.png","rockscluster.png","rofreesbie.png",
    "root.png","roslims.png","rpath.png","rpmlive.png","rubix.png","rubyx.png","runt.png",
    "runtu.png","sabayon.png","sabily.png","saline.png","salix.png","salvare.png","sam.png",
    "samity.png","santafe.png","satux.png","saxenos.png","schillix.png","sci.png",
    "scientific.png","sco.png","securepoint.png","sentinix.png","sentryfirewall.png",
    "shabdix.png","shark.png","shift.png","skolelinux.png","slackintosh.png","slackware.png",
    "slamd64.png","slampp.png","slavix.png","slax.png","slitaz.png","slix.png",
    "slotech.png","slynux.png","smartpeer.png","smeserver.png","smoothwall.png","sms.png",
    "snappix.png","snofrix.png","sol.png","solaris.png","sorcerer.png","sot.png",
    "sourcemage.png","soyombo.png","specifix.png","spectra.png","sphinxos.png",
    "squiggleos.png","stampede.png","startcom.png","std.png","storm.png","stresslinux.png",
    "stux.png","sulix.png","sunjds.png","sunwah.png","supergamer.png","superos.png",
    "superrescue.png","suriyan.png","suse.png","swecha.png","swift.png","syllable.png",
    "symphony.png","systemrescue.png","t2.png","ta.png","tablix.png","tao.png",
    "taprobane.png","tech.png","tfm.png","thepacketmaster.png","thinstation.png",
    "thisk.png","thiz.png","tilix.png","tinycore.png","tinyme.png","tinysofa.png",
    "toorox.png","topologilinux.png","toutou.png","triance.png","trinity.png","trisquel.png",
    "trixbox.png","troppix.png","truebsd.png","trustix.png","truva.png","trx.png",
    "tugux.png","tumix.png","tupiserver.png","tuquito.png","turbolinux.png","turkix.png",
    "turnkey.png","uberstudent.png","ubuntu.png","ubuntuce.png","ubuntupr.png",
    "ubunturescue.png","ubuntustudio.png","ufficiozero.png","uhu.png","ulite.png",
    "ulteo.png","ultima.png","ultimate.png","underground.png","united.png","unity.png",
    "untangle.png","uos.png","userful.png","userlinux.png","ututo.png","vector-old.png",
    "vector.png","venenux.png","vidalinux.png","videolinux.png","vine.png","vinux.png",
    "virtual.png","virux.png","vlos.png","vmknoppix.png","vnlinux.png","voltalinux.png",
    "voodoo.png","vortexbox.png","vyatta.png","wattos.png","wazobia.png","webconverger.png",
    "whitebox.png","whoppix.png","wienux.png","wifislax.png","winbi.png","wolvix.png",
    "womp.png","wow.png","xandros.png","xange.png","xarnoppix.png","xevian.png","xfld.png",
    "xinalta.png","xos.png","xpud.png","xteam.png","xubuntu.png","yellowdog.png",
    "yggdrasil.png","ylmf.png","yoper.png","youresale.png","zen.png","zencafe.png",
    "zenix.png","zentyal.png","zenwalk.png","zerahstar.png","zeroshell.png","zeus.png",
    "zevenos.png","zonecd.png","zopix.png","zorin.png",
]

_LUTGARU_RAW = "https://raw.githubusercontent.com/lutgaru/linux-distro-logos/master/"


class LogoRepoBrowserDialog(ctk.CTkToplevel):
    """
    Browses the lutgaru/linux-distro-logos repository (729 PNG logos).
    Lets the user search, preview, and import logos into the icons/ folder.
    """

    def __init__(self, parent, icons_dir: str, on_import: callable = None):
        super().__init__(parent)
        self.title(t('Dépôt de logos — lutgaru/linux-distro-logos'))
        self.geometry("820x560")
        self.resizable(True, True)
        self.after(100, self.lift)
        self.after(150, self.focus_force)

        self._icons_dir = icons_dir
        self._on_import = on_import
        self._preview_image = None   # référence ctk.CTkImage pour éviter le GC
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._schedule_refresh())
        self._refresh_job = None     # handle pour le debounce
        self._current_files: list[str] = []  # liste filtrée courante
        self._logo_btns: dict[str, ctk.CTkButton] = {}  # fname → bouton

        self._build()
        self._refresh_list()

    # ─────────────────────── BUILD ──────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── Liste gauche ─────────────────────────────────────────────────────
        left = ctk.CTkFrame(self, width=270)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left.grid_propagate(False)
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text=t('729 logos disponibles'),
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")

        ctk.CTkEntry(
            left, textvariable=self._search_var,
            placeholder_text=t('🔍 Rechercher...'),
            font=ctk.CTkFont(size=12), height=30
        ).grid(row=1, column=0, padx=8, pady=(0, 4), sticky="ew")

        self._listbox = ctk.CTkScrollableFrame(left)
        self._listbox.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        self._listbox.grid_columnconfigure(0, weight=1)

        self._lbl_count = ctk.CTkLabel(left, text="", font=ctk.CTkFont(size=10),
                                        text_color="gray55")
        self._lbl_count.grid(row=3, column=0, padx=8, pady=(2, 8))

        # ── Right-hand preview panel ──────────────────────────────────
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self._lbl_preview_name = ctk.CTkLabel(
            right, text=t('← Sélectionnez un logo'),
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self._lbl_preview_name.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")

        self._preview_frame = ctk.CTkFrame(right, fg_color="#1e1e2e", corner_radius=12)
        self._preview_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        self._preview_frame.grid_columnconfigure(0, weight=1)
        self._preview_frame.grid_rowconfigure(0, weight=1)

        self._lbl_preview_img = ctk.CTkLabel(
            self._preview_frame, text="",
            font=ctk.CTkFont(size=48), text_color="gray40"
        )
        self._lbl_preview_img.grid(row=0, column=0, padx=20, pady=40)

        self._lbl_preview_status = ctk.CTkLabel(
            right, text="", font=ctk.CTkFont(size=11), text_color="gray60"
        )
        self._lbl_preview_status.grid(row=2, column=0, padx=10, pady=2)

        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.grid(row=3, column=0, pady=(4, 12))

        self._btn_import_one = ctk.CTkButton(
            btn_row, text=t('⬆  Importer vers icons/'),
            fg_color="#1a5276", hover_color="#154360",
            width=180, height=34, state="disabled",
            command=self._import_selected
        )
        self._btn_import_one.pack(side="left", padx=6)

        ctk.CTkButton(btn_row, text=t('Fermer'), command=self.destroy,
                      width=90, height=34).pack(side="left", padx=6)

        # State
        self._selected_file: Optional[str] = None

    # ─────────────────────── LIST ────────────────────────────────────────────

    def _schedule_refresh(self):
        """Debounce: waits 250 ms after the last keystroke before rebuilding the list."""
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = self.after(250, self._refresh_list)

    # ── Couleurs de la liste ─────────────────────────────────────────────────
    _COL = {
        "normal":        ("transparent", "#2a2a3e"),
        "installed":     ("#1e3a24",     "#2d4a32"),
        "selected":      ("#1a3264",     "#1e3a78"),
        "sel_installed": ("#1a4040",     "#1e4a50"),
    }

    def _bind_scroll_repo(self, widget):
        """Forwarde la molette vers le canvas interne du CTkScrollableFrame."""
        try:
            canvas = self._listbox._parent_canvas
        except AttributeError:
            return
        # One "unit" per event regardless of e.delta's magnitude — see
        # _bind_table_scroll's comment.
        widget.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"),
                    add="+")
        widget.bind("<Button-4>",
                    lambda e: canvas.yview_scroll(-1, "units"),
                    add="+")
        widget.bind("<Button-5>",
                    lambda e: canvas.yview_scroll(1, "units"),
                    add="+")

    def _refresh_list(self):
        self._refresh_job = None
        query = self._search_var.get().strip().lower()

        for w in self._listbox.winfo_children():
            w.destroy()
        self._logo_btns.clear()

        self._current_files = [f for f in _LUTGARU_FILES
                                if not query or query in f[:-4].lower()]
        self._lbl_count.configure(text=f"{len(self._current_files)}{t(' résultat(s)')}")

        self._render_batch(0)

    def _render_batch(self, start: int):
        """Rend au plus 40 boutons, puis reprogramme le lot suivant via after()."""
        if not self.winfo_exists():
            return
        files = self._current_files
        BATCH = 40
        end = min(start + BATCH, len(files))

        for i in range(start, end):
            fname = files[i]
            name  = fname[:-4]
            installed = os.path.isfile(os.path.join(self._icons_dir, fname))
            selected  = (fname == self._selected_file)

            if selected:
                col_key = "sel_installed" if installed else "selected"
                prefix  = "▶  "
                tc      = "#2ecc71" if installed else "#ffffff"
            else:
                col_key = "installed" if installed else "normal"
                prefix  = "✓  " if installed else "   "
                tc      = "#2ecc71" if installed else "white"

            fg, hov = self._COL[col_key]
            btn = ctk.CTkButton(
                self._listbox,
                text=prefix + name, anchor="w",
                font=ctk.CTkFont(size=12),
                fg_color=fg, hover_color=hov,
                text_color=tc,
                corner_radius=5,
                command=lambda f=fname: self._select_logo(f)
            )
            btn.grid(row=i, column=0, sticky="ew", padx=4, pady=1)
            self._logo_btns[fname] = btn
            self._bind_scroll_repo(btn)

        if end < len(files):
            self.after(0, lambda: self.winfo_exists() and self._render_batch(end))

    # ─────────────────────── PREVIEW ─────────────────────────────────────────

    def _select_logo(self, filename: str):
        prev = self._selected_file
        self._selected_file = filename

        # ── Reset visuel de l'ancien bouton ──────────────────────────────────
        if prev and prev in self._logo_btns:
            installed = os.path.isfile(os.path.join(self._icons_dir, prev))
            col_key = "installed" if installed else "normal"
            fg, hov = self._COL[col_key]
            tc  = "#2ecc71" if installed else "white"
            pfx = "✓  " if installed else "   "
            self._logo_btns[prev].configure(
                fg_color=fg, hover_color=hov,
                text_color=tc, text=pfx + prev[:-4]
            )

        # ── Mise en surbrillance du nouveau bouton ───────────────────────────
        if filename in self._logo_btns:
            installed = os.path.isfile(os.path.join(self._icons_dir, filename))
            col_key = "sel_installed" if installed else "selected"
            fg, hov = self._COL[col_key]
            tc  = "#2ecc71" if installed else "#ffffff"
            self._logo_btns[filename].configure(
                fg_color=fg, hover_color=hov,
                text_color=tc, text="▶  " + filename[:-4]
            )

        # ── Starts loading the preview ──────────────────────────────────
        name = filename[:-4]
        self._lbl_preview_name.configure(text=name)
        self._lbl_preview_img.configure(text="⏳", image=None)
        self._preview_image = None
        self._lbl_preview_status.configure(text=t('Chargement de l\'aperçu...'))
        self._btn_import_one.configure(state="disabled")

        def load():
            url = _LUTGARU_RAW + filename
            try:
                import requests
                from PIL import Image
                import io
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                # Calcule la taille d'affichage (max 200x200, proportionnel)
                w, h = img.size
                scale = min(200 / w, 200 / h, 1.0)
                disp_w, disp_h = max(1, int(w * scale)), max(1, int(h * scale))
                thumb = img.resize((disp_w, disp_h), Image.LANCZOS)
                ctk_img = ctk.CTkImage(light_image=thumb, dark_image=thumb,
                                       size=(disp_w, disp_h))
                self.after(0, lambda img=ctk_img, fn=filename:
                           self.winfo_exists() and self._show_preview(img, fn))
            except Exception as e:
                self.after(0, lambda msg=str(e)[:80]:
                           self.winfo_exists() and self._show_preview_error(msg))

        threading.Thread(target=load, daemon=True).start()

    def _show_preview(self, ctk_img, filename: str):
        self._preview_image = ctk_img
        self._lbl_preview_img.configure(image=ctk_img, text="")
        installed = os.path.isfile(os.path.join(self._icons_dir, filename))
        status = t('✓ Déjà installé dans icons/') if installed else f"{t('Source : ')}{_LUTGARU_RAW}"
        self._lbl_preview_status.configure(
            text=status,
            text_color="#2ecc71" if installed else "gray60"
        )
        self._btn_import_one.configure(state="normal")

    def _show_preview_error(self, msg: str):
        self._lbl_preview_img.configure(text="✗", image=None)
        self._preview_image = None
        self._lbl_preview_status.configure(
            text=f"{t('Erreur : ')}{msg}", text_color="#e74c3c")

    # ─────────────────────── IMPORT ──────────────────────────────────────────

    def _import_selected(self):
        if not self._selected_file:
            return
        filename = self._selected_file
        url = _LUTGARU_RAW + filename
        dest = os.path.join(self._icons_dir, filename)
        self._btn_import_one.configure(state="disabled", text=t('Téléchargement...'))

        def run():
            ok, err = download_logo(url=url, dest_path=dest, size=(128, 128))
            if ok:
                def on_ok(fn=filename):
                    if not self.winfo_exists():
                        return
                    self._lbl_preview_status.configure(
                        text=f"{t('✓ Importé : ')}{fn}", text_color="#2ecc71")
                    # Updates the button in the list without rebuilding everything
                    if fn in self._logo_btns:
                        fg, hov = self._COL["sel_installed"]
                        self._logo_btns[fn].configure(
                            fg_color=fg, hover_color=hov,
                            text_color="#2ecc71", text="▶  " + fn[:-4]
                        )
                self.after(0, on_ok)
                if self._on_import:
                    self.after(0, lambda: self.winfo_exists() and self._on_import())
            else:
                self.after(0, lambda e=err: self.winfo_exists() and
                           show_error(
                               self, t('Erreur'), f"{t('Impossible de télécharger :\n')}{e}"))
            self.after(0, lambda: self.winfo_exists() and self._btn_import_one.configure(
                state="normal", text=t('⬆  Importer vers icons/')))

        threading.Thread(target=run, daemon=True).start()


def _read_app_version() -> str:
    """Reads the version from pyproject.toml to avoid a duplicate source of truth."""
    try:
        import tomllib
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "pyproject.toml"), "rb") as f:
            return tomllib.load(f)["project"]["version"]
    except Exception:
        return "1.0.0"


# ══════════════════════════════════════════════════════════════════════════════
#  Dialog: About
# ══════════════════════════════════════════════════════════════════════════════

class AboutDialog(ctk.CTkToplevel):

    _VERSION = _read_app_version()
    _LINKS = {
        "github":      "https://github.com/celmax85",
        "twitch":      "https://twitch.tv/celmax85",
        "bmc":         "https://www.buymeacoffee.com/celmax",
        "assistant":   "https://share.google/zQNU93G1AKP804lZW",
    }

    def __init__(self, parent):
        super().__init__(parent)
        self.title(t('À propos de VentoyIsoUpdater'))
        self.geometry("480x560")
        self.resizable(False, False)
        self.after(100, self.lift)
        self.after(150, self.focus_force)
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        row = 0

        # ── Icon ────────────────────────────────────────────────────────────
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "icon.png"
        )
        if os.path.isfile(icon_path):
            try:
                from PIL import Image
                img = Image.open(icon_path).convert("RGBA").resize((64, 64), Image.LANCZOS)
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(64, 64))
                ctk.CTkLabel(self, image=ctk_img, text="").grid(
                    row=row, column=0, pady=(20, 6))
                row += 1
            except Exception:
                pass

        # ── Titre + version ──────────────────────────────────────────────────
        ctk.CTkLabel(self, text="VentoyIsoUpdater",
                     font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=row, column=0, pady=(0, 2)); row += 1

        ctk.CTkLabel(self, text=f"Version {self._VERSION}{t('  —  Licence MIT')}",
                     font=ctk.CTkFont(size=11), text_color="gray60"
                     ).grid(row=row, column=0, pady=(0, 4)); row += 1

        ctk.CTkLabel(
            self,
            text=t("Gérez vos clés Ventoy : vérification des mises à jour ISO,\ntéléchargements et gestion du thème GRUB2."),
            font=ctk.CTkFont(size=12), wraplength=400, justify="center",
        ).grid(row=row, column=0, padx=20, pady=(0, 12)); row += 1

        # ── Author separator ────────────────────────────────────────────────
        ctk.CTkFrame(self, height=1, fg_color="gray30"
                     ).grid(row=row, column=0, sticky="ew", padx=30, pady=4); row += 1

        ctk.CTkLabel(self, text=t('Créé par Maxence Baffet  ·  alias celmax'),
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=row, column=0, pady=(6, 10)); row += 1

        # ── Social links ──────────────────────────────────────────────────
        socials = ctk.CTkFrame(self, fg_color="transparent")
        socials.grid(row=row, column=0, pady=(0, 6)); row += 1

        ctk.CTkButton(
            socials, text="  GitHub", width=120, height=32,
            fg_color="#24292e", hover_color="#444d56",
            command=lambda: webbrowser.open(self._LINKS["github"])
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            socials, text="  Twitch", width=120, height=32,
            fg_color="#6441a5", hover_color="#7d5bbe",
            command=lambda: webbrowser.open(self._LINKS["twitch"])
        ).pack(side="left", padx=6)

        ctk.CTkLabel(socials, text=t('Discord : celmax'),
                     font=ctk.CTkFont(size=12), text_color="#5865F2"
                     ).pack(side="left", padx=8)

        # ── Buy me a coffee ──────────────────────────────────────────────────
        ctk.CTkButton(
            self, text=t('☕  Buy me a coffee'), width=200, height=36,
            fg_color="#FFDD00", hover_color="#e6c800",
            text_color="#000000", font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: webbrowser.open(self._LINKS["bmc"])
        ).grid(row=row, column=0, pady=(4, 8)); row += 1

        # ── Separator ───────────────────────────────────────────────────────
        ctk.CTkFrame(self, height=1, fg_color="gray30"
                     ).grid(row=row, column=0, sticky="ew", padx=30, pady=4); row += 1

        # ── Assistant Numérique d'Anjou ────────────────────────────────────────
        ctk.CTkButton(
            self,
            text=t('Mon Assistant Numérique d\'Anjou'),
            width=280, height=32,
            fg_color="transparent", hover_color="gray25",
            border_width=1, border_color="gray50",
            text_color="gray80", font=ctk.CTkFont(size=12),
            command=lambda: webbrowser.open(self._LINKS["assistant"])
        ).grid(row=row, column=0, pady=(4, 4)); row += 1

        # ── Boutons bas ──────────────────────────────────────────────────────
        ctk.CTkButton(
            self, text=t('Fermer'), width=100,
            fg_color="gray40", hover_color="gray30",
            command=self.destroy
        ).grid(row=row, column=0, pady=(10, 16)); row += 1
