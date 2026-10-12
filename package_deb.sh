#!/usr/bin/env bash
# Builds a .deb package for Debian/Ubuntu/Mint/PopOS…
# Usage: ./package_deb.sh

set -e

APP_NAME="VentoyIsoUpdater"
PKG_NAME="ventoy-iso-updater"
APP_ID="com.github.celmax85.ventoyisoupdater"
VERSION="${VERSION:-$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)}"   # overridable: VERSION=1.2.3 ./package_deb.sh
ARCH="amd64"
MAINTAINER="Maxence Baffet <celomaxge85@gmail.com>"
DESCRIPTION="Desktop GUI for managing Ventoy USB drives"
BINARY="dist/linux/$APP_NAME"

if [ ! -f "$BINARY" ]; then
    echo "ERROR: $BINARY not found. Run ./build.sh first."
    exit 1
fi

echo "==> Building the .deb package..."

PKG_DIR="build/deb/${PKG_NAME}_${VERSION}_${ARCH}"
rm -rf "$PKG_DIR"

# ── Binary ───────────────────────────────────────────────────────────────────

mkdir -p "$PKG_DIR/usr/bin"
cp "$BINARY" "$PKG_DIR/usr/bin/$APP_NAME"
chmod 755 "$PKG_DIR/usr/bin/$APP_NAME"

# ── Multi-resolution icons ───────────────────────────────────────────────────

for size in 16 32 48 64 128 256; do
    mkdir -p "$PKG_DIR/usr/share/icons/hicolor/${size}x${size}/apps"
    if command -v convert &>/dev/null; then
        convert "assets/icon.png" -resize "${size}x${size}" \
            "$PKG_DIR/usr/share/icons/hicolor/${size}x${size}/apps/$APP_NAME.png" 2>/dev/null \
            || cp "assets/icon.png" "$PKG_DIR/usr/share/icons/hicolor/${size}x${size}/apps/$APP_NAME.png"
    else
        cp "assets/icon.png" "$PKG_DIR/usr/share/icons/hicolor/${size}x${size}/apps/$APP_NAME.png"
    fi
done

mkdir -p "$PKG_DIR/usr/share/pixmaps"
cp "assets/icon.png" "$PKG_DIR/usr/share/pixmaps/$APP_NAME.png"

# ── .desktop file ──────────────────────────────────────────────────────────────
# BAMF_DESKTOP_FILE_HINT: explicitly tells GNOME/Unity which .desktop file
# to associate with the window -> correct icon in the taskbar/dock.

mkdir -p "$PKG_DIR/usr/share/applications"
cat > "$PKG_DIR/usr/share/applications/$APP_NAME.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$APP_NAME
GenericName=Ventoy Drive Manager
Comment=$DESCRIPTION
Exec=env BAMF_DESKTOP_FILE_HINT=/usr/share/applications/$APP_NAME.desktop /usr/bin/$APP_NAME
Icon=$APP_NAME
Terminal=false
Categories=Utility;System;
Keywords=ventoy;usb;iso;linux;boot;
StartupWMClass=$APP_NAME
StartupNotify=true
EOF
chmod 644 "$PKG_DIR/usr/share/applications/$APP_NAME.desktop"

# ── AppStream metainfo ────────────────────────────────────────────────────────
# Needed so GNOME Software / Ubuntu Software Center shows the icon and
# description when installing the package.

mkdir -p "$PKG_DIR/usr/share/metainfo"
cat > "$PKG_DIR/usr/share/metainfo/$APP_ID.metainfo.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>$APP_ID</id>
  <name>VentoyIsoUpdater</name>
  <summary>$DESCRIPTION</summary>
  <description>
    <p>
      VentoyIsoUpdater is a desktop GUI for managing your Ventoy USB drives.
      Check for ISO updates, download new versions, and manage themes
      directly from the app.
    </p>
  </description>
  <icon type="stock">$APP_NAME</icon>
  <launchable type="desktop-id">$APP_NAME.desktop</launchable>
  <url type="homepage">https://github.com/celmax85</url>
  <metadata_license>MIT</metadata_license>
  <project_license>MIT</project_license>
  <content_rating type="oars-1.1" />
  <releases>
    <release version="$VERSION" date="$(date +%Y-%m-%d)" />
  </releases>
  <provides>
    <binary>$APP_NAME</binary>
  </provides>
  <categories>
    <category>Utility</category>
    <category>System</category>
  </categories>
  <keywords>
    <keyword>ventoy</keyword>
    <keyword>usb</keyword>
    <keyword>iso</keyword>
    <keyword>boot</keyword>
  </keywords>
</component>
EOF

# ── DEBIAN/control ────────────────────────────────────────────────────────────

INSTALLED_SIZE=$(du -sk "$PKG_DIR" | cut -f1)

mkdir -p "$PKG_DIR/DEBIAN"
cat > "$PKG_DIR/DEBIAN/control" << EOF
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Installed-Size: $INSTALLED_SIZE
Maintainer: $MAINTAINER
Description: $DESCRIPTION
 VentoyIsoUpdater is a desktop GUI for managing Ventoy USB drives.
 ISO update checks, downloads, theme management.
EOF

# ── postinst / postrm ─────────────────────────────────────────────────────────

cat > "$PKG_DIR/DEBIAN/postinst" << 'EOF'
#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi
if command -v appstreamcli >/dev/null 2>&1; then
    appstreamcli refresh --force 2>/dev/null || true
fi
EOF
chmod 755 "$PKG_DIR/DEBIAN/postinst"

cat > "$PKG_DIR/DEBIAN/postrm" << 'EOF'
#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi
if command -v appstreamcli >/dev/null 2>&1; then
    appstreamcli refresh --force 2>/dev/null || true
fi
EOF
chmod 755 "$PKG_DIR/DEBIAN/postrm"

# ── Build ─────────────────────────────────────────────────────────────────────

OUTPUT="dist/linux/${PKG_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$PKG_DIR" "$OUTPUT"

echo ""
echo "==> .deb package generated: $OUTPUT"
echo "    sudo dpkg -i $OUTPUT"
echo "    Uninstall: sudo apt remove $PKG_NAME"
