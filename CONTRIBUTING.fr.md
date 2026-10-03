🇬🇧 [English](CONTRIBUTING.md) · 🇫🇷 **Français**

# Contribuer

## Ajouter une distribution

Voir la [structure du projet](README.fr.md#structure-du-projet) dans le README
pour l'organisation générale. Ajouter une distribution demande trois
modifications :

1. Un fichier `sources/<distro>.py` avec un checker qui hérite de
   `BaseChecker` (`sources/base.py`).
2. Son import **et** son entrée dans le registre des checkers, dans
   `core/version_checker.py::_load_checkers()` — facile à oublier : sans
   elle, la distro est reconnue sur la clé mais sa version n'est jamais
   vérifiée.
3. Une ou plusieurs entrées dans `data/distros.json` (une par édition ou
   architecture).

`sources/` est la surface la plus exposée du projet aux contributions
externes : c'est du code qui fait des requêtes réseau vers des sites tiers et
qui applique des expressions régulières à des noms de fichiers. Avant
d'ouvrir une PR sur `sources/` ou `data/distros.json`, merci de vérifier :

- **HTTPS uniquement.** Pas d'URL `http://` sauf impossibilité technique
  documentée dans un commentaire (cas réel : le certificat TLS de
  `download.proxmox.com` ne correspond pas à son propre nom d'hôte — voir
  `sources/proxmox.py`, qui utilise `enterprise.proxmox.com` à la place).
- **`timeout=` obligatoire** sur chaque appel `requests.get`/`head`/`post`.
- **Jamais `shell=True`**, `eval`, `exec`, `pickle` ou `yaml.load`.
- **Empreinte réelle si disponible.** Si la source publie un fichier de
  sommes de contrôle (`SHA256SUMS`, `CHECKSUM`, `*.sha256`…), renseignez le
  champ `checksum=` de `VersionInfo` — pas seulement `checksum_type=`, qui
  seul ne déclenche aucune vérification (voir `core/downloader.py`). Les
  helpers `sources/_checksum.py::fetch_sha256sums` /
  `fetch_bsd_sha256` couvrent les deux formats les plus courants. Si aucune
  empreinte n'est publiée, ne renseignez ni l'un ni l'autre plutôt que de
  laisser croire à une vérification qui n'a pas lieu.
- **Regex sans backtracking catastrophique.** Évitez les motifs imbriqués du
  type `(.*)+` ou `(\d+)+` appliqués à du texte non fiable (nom de fichier
  scanné sur la clé, réponse HTML d'un site tiers) — préférez des classes de
  caractères précises (`[\d.]+`, etc.), comme le fait déjà le reste de
  `sources/`.
- **Échecs journalisés, pas seulement avalés.** `except Exception: return
  None`/`[]` est le pattern attendu (une source injoignable ne doit pas
  planter l'appli), mais ajoutez un `logger.debug(...)` (voir
  `core/logger.py`) pour qu'un vrai bug reste diagnosticable plutôt
  qu'indiscernable d'un site simplement indisponible.
- **`logo_url` doit pointer vers une image matricielle (PNG/JPG/WEBP/ICO),
  jamais un `.svg`.** `core/downloader.py::download_logo` l'ouvre avec PIL,
  qui ne sait pas décoder les formats vectoriels — ça échoue avec une
  erreur assez opaque (`cannot identify image file`) au moment de
  l'exécution, pas à la revue de code. Le site officiel d'un projet n'a
  souvent que du SVG pour son logo ; dans ce cas, utilisez une alternative
  matricielle (favicon, avatar d'organisation GitHub sur
  `https://github.com/<org>.png`, etc.) — ou si rien ne convient,
  embarquez un vrai PNG dans `assets/logos/` et référencez-le avec le
  préfixe `local:<fichier>`, comme `proxmox.png` / `Pop!OS.png`.

## Tests

```bash
.venv/bin/pytest tests/ -v
```

Toute nouvelle logique dans `core/` mérite un test dans `tests/`. Les
vérificateurs de `sources/` ne sont pas unitairement testés un par un (ce
serait 64 suites dépendantes du réseau) — `tests/test_version_checker.py`
et `tests/test_base_checker.py` couvrent le contrat commun (`BaseChecker`,
`VersionInfo`, orchestration).
