"""
Minimal internationalization for the GUI.

French is the source language: every string displayed in gui/app.py is
written in French directly in the code, and t("...") is the extension point
that translates it on the fly if the active language isn't "fr". This
choice (rather than abstract keys like t("MSG_001")) keeps the code
readable and French functional even when a translation is missing: t()
simply returns the text as-is if no entry matches.

The language is persisted in preferences (core/preferences.py) and read on
startup. Changing it from within the app requires a restart to apply
everywhere (see AboutDialog / the language selector): dynamically
rebuilding the ~60 already-instantiated windows/dialogs would be far more
fragile than simply re-reading the preference on next launch.
"""

from typing import Optional

_current_lang = "fr"

SUPPORTED_LANGUAGES = {"fr": "Français", "en": "English"}


def set_language(lang: str) -> None:
    global _current_lang
    if lang in SUPPORTED_LANGUAGES:
        _current_lang = lang


def get_language() -> str:
    return _current_lang


def t(text: str, **kwargs) -> str:
    """
    Translates `text` (written in French in the calling code) into the
    active language. Returns the French text unchanged if the active
    language is French or if no translation is registered for this text.

    kwargs: values passed to str.format() after translation, for messages
    containing positional/named placeholders (e.g.
    t("Fichier {name} introuvable", name=filename)).
    """
    if _current_lang != "fr":
        text = _TRANSLATIONS.get(_current_lang, {}).get(text, text)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # --- Fragments / suffixes / connecteurs -----------------------------
        "\n\n  Version : ": "\n\n  Version: ",
        "\n\nL'ancienne ISO ne sera PAS supprimée automatiquement.": "\n\nThe old ISO will NOT be deleted automatically.",
        "\n\nLe fichier sera téléchargé dans :\n": "\n\nThe file will be downloaded to:\n",
        "\n\nScript exécuté avec les droits administrateur :\n  ": "\n\nScript run with administrator rights:\n  ",
        "\n\nVérifiez que le fichier data/distros.json est présent et valide.": "\n\nCheck that the data/distros.json file is present and valid.",
        "\n\n⚠  TOUTES LES DONNÉES SERONT EFFACÉES !\n\nContinuer ?": "\n\n⚠  ALL DATA WILL BE ERASED!\n\nContinue?",
        "\n   Dernière version disponible : v": "\n   Latest available version: v",
        "\n  Disponible : ": "\n  Available: ",
        "\n  Nouveau :  ": "\n  New:  ",
        "\n⚠ Sans icône : ": "\n⚠ No icon: ",
        "  Dernière version : v": "  Latest version: v",
        "  —  Licence MIT": "  —  MIT License",
        " ISO trouvée(s). Cliquez sur 'Vérifier tout'.": " ISO(s) found. Click 'Check all'.",
        " ISO(s) à télécharger.": " ISO(s) to download.",
        " PNG composé(s) · ": " composed PNG(s) · ",
        " a été téléchargé.\n\nCette source ne publie pas d'empreinte automatiquement vérifiable — pensez à contrôler l'intégrité de l'ISO vous-même avant de l'utiliser :\n\n": " has been downloaded.\n\nThis source doesn't publish an automatically verifiable checksum — please check the ISO's integrity yourself before using it:\n\n",
        " icône(s) PNG": " PNG icon(s)",
        " image(s) PNG": " PNG image(s)",
        " logo(s) OK": " logo(s) OK",
        " logo(s) importé(s).\nÉchecs :\n": " logo(s) imported.\nFailures:\n",
        " logo(s) manquant(s) — cliquez pour télécharger": " missing logo(s) — click to download",
        " prêt à l'installation.": " ready to install.",
        " résultat(s)": " result(s)",
        " téléchargé(s))": " downloaded)",
        " échec(s)": " failure(s)",
        " — aucun PNG trouvé": " — no PNG found",
        "# Erreur de lecture : ": "# Read error: ",
        "# theme.txt introuvable dans ce dossier.": "# theme.txt not found in this folder.",
        "(icons/ introuvable)": "(icons/ not found)",
        "(theme.txt introuvable)": "(theme.txt not found)",
        "729 logos disponibles": "729 logos available",
        "Arch Linux": "Arch Linux",
        "Aucun dossier icons/ détecté sur la clé.": "No icons/ folder detected on the drive.",
        "Aucun logo PNG trouvé.\nUtilisez 'Importer PNG(s)' pour en ajouter.": "No PNG logo found.\nUse 'Import PNG(s)' to add some.",
        "Aucun résultat.": "No results.",
        "Aucun thème détecté sur la clé": "No theme detected on the drive",
        "Aucun thème détecté sur la clé.": "No theme detected on the drive.",
        "Aucune clé USB détectée": "No USB drive detected",
        "Aucune clé Ventoy détectée": "No Ventoy drive detected",
        "Aucune clé Ventoy trouvée.": "No Ventoy drive found.",
        "Aucune ISO sélectionnée.": "No ISO selected.",
        "Aucune image PNG trouvée à la racine du thème.": "No PNG image found at the theme's root.",
        "Aucune version stable trouvée.\nDésactivez le filtre pour voir toutes les versions.": "No stable version found.\nDisable the filter to see all versions.",
        "Aucune version stable.\nDésactivez le filtre pour voir toutes les versions.": "No stable version.\nDisable the filter to see all versions.",
        "Aucune version trouvée ou source inaccessible.": "No version found or source unreachable.",
        "Aucune version trouvée.": "No version found.",
        "Branchez une clé USB et actualisez.": "Plug in a USB drive and refresh.",
        "Chargement de l'aperçu...": "Loading preview...",
        "Chargement des versions...": "Loading versions...",
        "Chargé.": "Loaded.",
        "Checker non disponible.": "Checker unavailable.",
        "Clé USB :": "USB drive:",
        "Clé Ventoy :": "Ventoy drive:",
        "Confirmer l'installation": "Confirm installation",
        "Confirmer le téléchargement": "Confirm download",
        "Créer une clé Ventoy": "Create a Ventoy drive",
        "Créé par Maxence Baffet  ·  alias celmax": "Created by Maxence Baffet  ·  aka celmax",
        "Debian 12": "Debian 12",
        "Dernière ver.": "Latest ver.",
        "Discord : celmax": "Discord: celmax",
        "Dossier :": "Folder:",
        "Dossier : ": "Folder: ",
        "Dossier icons/ absent du thème": "icons/ folder missing from the theme",
        "Dossier icons/ introuvable sur la clé.": "icons/ folder not found on the drive.",
        "Dossier icons/ introuvable.": "icons/ folder not found.",
        "Démarrage de l'installation Ventoy...\n": "Starting Ventoy installation...\n",
        "Dépôt de logos — lutgaru/linux-distro-logos": "Logo repository — lutgaru/linux-distro-logos",
        "En attente": "Pending",
        "Erreur : ": "Error: ",
        "Erreur : ventoy2disk.sh introuvable dans l'archive.": "Error: ventoy2disk.sh not found in the archive.",
        "Erreur : ventoy2disk.sh introuvable.": "Error: ventoy2disk.sh not found.",
        "Erreur critique": "Critical error",
        "Erreur inattendue": "Unexpected error",
        "Espace insuffisant": "Not enough space",
        "Espace insuffisant sur le disque :\n  Nécessaire : ": "Not enough disk space:\n  Required: ",
        "Fedora 42 Workstation": "Fedora 42 Workstation",
        "Fichier : ": "File: ",
        "Fichier ISO": "ISO file",
        "Fichier absent.": "File missing.",
        "File d'attente (0)": "Download queue (0)",
        "File d'attente": "Download queue",
        "Gérez vos clés Ventoy : vérification des mises à jour ISO,\ntéléchargements et gestion du thème GRUB2.": "Manage your Ventoy drives: ISO update checks,\ndownloads, and GRUB2 theme management.",
        "ISO : ": "ISO: ",
        "ISO : —": "ISO: —",
        "Images PNG": "PNG images",
        "Importer des images PNG": "Import PNG images",
        "Importer des logos PNG": "Import PNG logos",
        "Impossible de charger la base de données des distributions :\n": "Unable to load the distribution database:\n",
        "Impossible de créer le dossier :\n": "Unable to create the folder:\n",
        "Impossible de récupérer la version Ventoy.": "Unable to retrieve the Ventoy version.",
        "Impossible de supprimer le fichier.": "Unable to delete the file.",
        "Impossible de télécharger :\n": "Unable to download:\n",
        "Installer Ventoy sur :\n\n  ": "Install Ventoy on:\n\n  ",
        "Journal de synchronisation des logos": "Logo sync log",
        "L'installation a échoué.\nConsultez la sortie ci-dessous.": "Installation failed.\nSee the output below.",
        "Label : ": "Label: ",
        "Label : —": "Label: —",
        "Le téléchargement de la file en cours doit se terminer avant de pouvoir fermer cette fenêtre.": "The current queue download must finish before this window can be closed.",
        "Les ISO Windows doivent être téléchargées\nmanuellement depuis le site Microsoft.": "Windows ISOs must be downloaded\nmanually from the Microsoft website.",
        "Libre : ": "Free: ",
        "Libre : —": "Free: —",
        "Logos : ": "Logos: ",
        "Logos thème": "Theme logos",
        "Mettre à jour :\n\n  Actuel  :  ": "Update:\n\n  Current  :  ",
        "Mon Assistant Numérique d'Anjou": "Mon Assistant Numérique d'Anjou",
        "PIL/Pillow requis pour l'aperçu.": "PIL/Pillow required for the preview.",
        "PNG du thème (fond d'écran, boutons…) — hors dossier icons/": "Theme PNGs (background, buttons…) — outside the icons/ folder",
        "Recherche des clés Ventoy...": "Searching for Ventoy drives...",
        "Recherche en cours...": "Searching...",
        "Rendu en cours…": "Rendering…",
        "Sauvegardé  (backup : ": "Saved  (backup: ",
        "Scan des clés USB...": "Scanning USB drives...",
        "Outils": "Tools",
        "Serveur / Réseau": "Server / Network",
        "Source : ": "Source: ",
        "Stables uniquement": "Stable only",
        "Succès": "Success",
        "Suivant →": "Next →",
        "Supprimer définitivement :\n": "Permanently delete:\n",
        "System Rescue": "System Rescue",
        "Sécurité": "Security",
        "Sélectionnez d'abord une clé Ventoy.": "Select a Ventoy drive first.",
        "Taille": "Size",
        "Thème": "Theme",
        "Thème : ": "Theme: ",
        "Thème : non trouvé": "Theme: not found",
        "Thème : —": "Theme: —",
        "Tous les fichiers": "All files",
        "Tous les logos sont présents ✓": "All logos are present ✓",
        "Téléchargement annulé par l'utilisateur": "Download cancelled by user",
        "Téléchargement en cours...": "Downloading...",
        "Téléchargement...": "Downloading...",
        "Télécharger une ISO": "Download an ISO",
        "Ubuntu 24.04 LTS": "Ubuntu 24.04 LTS",
        "Ventoy : v": "Ventoy: v",
        "Ventoy : —": "Ventoy: —",
        "Ventoy non trouvé localement.": "Ventoy not found locally.",
        "Ventoy vous permet de démarrer plusieurs ISO depuis une seule clé USB.": "Ventoy lets you boot several ISOs from a single USB drive.",
        "VentoyIsoUpdater démarré": "VentoyIsoUpdater started",
        "VentoyIsoUpdater fermé": "VentoyIsoUpdater closed",
        "Ver. locale": "Local ver.",
        "Version Ventoy :": "Ventoy version:",
        "Versions de ": "Versions of ",
        "Versions disponibles — ": "Available versions — ",
        "Vérification en cours...": "Checking...",
        "Vérification manuelle recommandée": "Manual verification recommended",
        "Vérification...": "Checking...",
        "Windows 11": "Windows 11",
        "theme.txt introuvable — impossible de sauvegarder.": "theme.txt not found — cannot save.",
        "thème": "theme",
        "À propos de VentoyIsoUpdater": "About VentoyIsoUpdater",
        "Échec : ": "Failure: ",
        "Échec du téléchargement ou de la vérification d'intégrité de l'archive. Réessayez.": "Download or integrity check of the archive failed. Try again.",
        "Éditeur de thème — ": "Theme editor — ",
        "ℹ  À propos": "ℹ  About",
        "← Précédent": "← Previous",
        "← Sélectionnez un logo": "← Select a logo",
        "← Sélectionnez une distro": "← Select a distro",
        "↺  Actualiser": "↺  Refresh",
        "↺  Re-télécharger tous les logos": "↺  Re-download all logos",
        "↺  Recharger": "↺  Reload",
        "↺ Actualiser": "↺ Refresh",
        "☕  Buy me a coffee": "☕  Buy me a coffee",
        "☰ Versions": "☰ Versions",
        "⚠  ATTENTION : L'installation de Ventoy effacera TOUTES les données\n   de la clé USB sélectionnée. Cette opération est irréversible.": "⚠  WARNING: Installing Ventoy will erase ALL data\n   on the selected USB drive. This operation is irreversible.",
        "⚠ Logo introuvable pour ": "⚠ Logo not found for ",
        "✓  Ventoy trouvé : ": "✓  Ventoy found: ",
        "✓  Ventoy v": "✓  Ventoy v",
        "✓ Déjà installé dans icons/": "✓ Already installed in icons/",
        "✓ Importé : ": "✓ Imported: ",
        "✓ Logo téléchargé : ": "✓ Logo downloaded: ",
        "✓ Terminé — ": "✓ Done — ",
        "✓ Terminé — Toutes les ISO reconnues sont à jour.": "✓ Done — All recognized ISOs are up to date.",
        "✓ Téléchargé : ": "✓ Downloaded: ",
        "✓ Ventoy installé avec succès !\n\nRetirez et rebranchez la clé USB puis actualisez VentoyIsoUpdater.": "✓ Ventoy installed successfully!\n\nUnplug and replug the USB drive, then refresh VentoyIsoUpdater.",
        "✓ ventoy.json mis à jour : /": "✓ ventoy.json updated: /",
        "✕ Annuler": "✕ Cancel",
        "⬆  Importer PNG(s)": "⬆  Import PNG(s)",
        "⬆  Importer un PNG": "⬆  Import a PNG",
        "⬆  Importer vers icons/": "⬆  Import to icons/",
        "⬇  Télécharger Ventoy": "⬇  Download Ventoy",
        "⬇  Télécharger logos manquants": "⬇  Download missing logos",
        "⬇  Télécharger une ISO": "⬇  Download an ISO",
        "⬇ Télécharger": "⬇ Download",
        "⬇ Télécharger tout": "⬇ Download all",
        "🌐  Dépôt de logos": "🌐  Logo repository",
        "🌐 Ouvrir le site Microsoft": "🌐 Open the Microsoft website",
        "🎨  Gérer le thème": "🎨  Manage theme",
        "🎨  Icônes distros": "🎨  Distro icons",
        "💾  Créer une clé Ventoy": "💾  Create a Ventoy drive",
        "💾  Installer Ventoy": "💾  Install Ventoy",
        "💾  Sauvegarder": "💾  Save",
        "📝  theme.txt": "📝  theme.txt",
        "🔄  Actualiser l'aperçu": "🔄  Refresh preview",
        "🔍  Vérifier tout": "🔍  Check all",
        "🔍 Filtrer...": "🔍 Filter...",
        "🔍 Rechercher...": "🔍 Search...",
        "🖥  Aperçu": "🖥  Preview",
        "🖼  Images": "🖼  Images",

        # --- Short words / buttons (outside the broad listing, added manually) --
        # core/version_checker.py::UpdateStatus values, displayed via
        # result.status.value in the ISO list's status column.
        "À jour": "Up to date",
        "Mise à jour disponible": "Update available",
        "Non reconnue": "Not recognized",
        "Vérification manuelle": "Manual check",

        "Langue": "Language",
        "La langue sera appliquée au prochain démarrage de VentoyIsoUpdater.": "The language will apply the next time VentoyIsoUpdater starts.",
        "Téléchargement annulé": "Download cancelled",
        "Format distros.json invalide : clé 'distros' manquante": "Invalid distros.json format: missing 'distros' key",
        "Fermer": "Close",
        "Impossible de démonter {devices} (occupé).\nFermez tout gestionnaire de fichiers ou toute fenêtre qui a la clé USB ouverte, puis réessayez.": "Could not unmount {devices} (busy).\nClose any file manager or window that has the USB drive open, then try again.",
        "Impossible d'obtenir les droits root.\nExécutez manuellement :\n  sudo {script} {flag} {device}": "Could not obtain root privileges.\nRun manually:\n  sudo {script} {flag} {device}",
        "Délai d'attente dépassé.": "Timed out.",
        "Droits administrateur requis.\nRelancez VentoyIsoUpdater en tant qu'administrateur.": "Administrator rights required.\nRestart VentoyIsoUpdater as administrator.",
        "Système non supporté.": "Unsupported system.",
        "Oui": "Yes",
        "Non": "No",
        "Aucun thème configuré": "No theme configured",
        "Cette clé Ventoy n'a pas encore de thème configuré — normal pour une installation qui n'a jamais démarré : rien ne le crée automatiquement.\n\nCréer une structure de thème par défaut pour commencer à la personnaliser ?": "This Ventoy drive has no theme configured yet — normal for an install that has never been booted: nothing creates one automatically.\n\nCreate a default theme structure to start customizing it?",
        "Impossible de créer la structure du thème.": "Could not create the theme structure.",
        "Journal :": "Log:",
        "📋  Copier": "📋  Copy",
        "✓  Copié": "✓  Copied",
        "Ventoy déjà présent": "Ventoy already present",
        "Ce disque contient déjà une installation de Ventoy.\n\nVoulez-vous forcer la réinstallation ?\n⚠  TOUTES LES DONNÉES SERONT EFFACÉES !": "This disk already contains a Ventoy installation.\n\nDo you want to force a reinstall?\n⚠  ALL DATA WILL BE ERASED!",
        "Erreur : impossible de vérifier l'intégrité de la copie de Ventoy en cache (hors ligne ou archive modifiée). Retéléchargez Ventoy.\n": "Error: could not verify the integrity of the cached Ventoy copy (offline or modified archive). Download Ventoy again.\n",
        "Autre": "Other",
        "Avertissement": "Warning",
        "Chargement...": "Loading...",
        "Installation...": "Installing...",
        "Erreur": "Error",
        "Dossier": "Folder",
        "Statut": "Status",
        "Supprimer": "Delete",
        "Remplacer": "Replace",
        "Remplacer ": "Replace ",
        "Annulation…": "Cancelling…",
        "inconnue": "unknown",
        " fichier(s)": " file(s)",
    }
}
