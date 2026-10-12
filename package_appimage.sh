#!/usr/bin/env bash
# Builds an AppImage — universal portable Linux format (zero install).
# Downloads appimagetool into tools/ if missing.
# Usage: ./package_appimage.sh

set -e

APP_NAME="VentoyIsoUpdater"
VERSION="${VERSION:-$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)}"   # overridable: VERSION=1.2.3 ./package_appimage.sh
BINARY="dist/linux/$APP_NAME"
APPIMAGE_TOOL="tools/appimagetool-x86_64.AppImage"
APPIMAGE_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"

if [ ! -f "$BINARY" ]; then
    echo "ERROR: $BINARY not found. Run ./build.sh first."
    exit 1
fi

if [ ! -f "$APPIMAGE_TOOL" ]; then
    echo "==> Downloading appimagetool into tools/..."
    mkdir -p tools
    curl -L -o "$APPIMAGE_TOOL" "$APPIMAGE_URL"
    chmod +x "$APPIMAGE_TOOL"
fi

echo "==> Building the AppImage..."

APPDIR="build/appimage/${APP_NAME}.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"

cp "$BINARY" "$APPDIR/usr/bin/$APP_NAME"
chmod 755 "$APPDIR/usr/bin/$APP_NAME"

cp "assets/icon.png" "$APPDIR/$APP_NAME.png"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp "assets/icon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP_NAME.png"

cat > "$APPDIR/$APP_NAME.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$APP_NAME
GenericName=Ventoy Drive Manager
Comment=Manage your Ventoy ISOs: update checks, downloads, themes
Exec=env BAMF_DESKTOP_FILE_HINT=${APPDIR}/$APP_NAME.desktop $APP_NAME
Icon=$APP_NAME
Terminal=false
Categories=Utility;System;
Keywords=ventoy;usb;iso;linux;boot;
StartupWMClass=$APP_NAME
StartupNotify=true
EOF

cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/sh
exec "$APPDIR/usr/bin/VentoyIsoUpdater" "$@"
EOF
chmod +x "$APPDIR/AppRun"

OUTPUT="dist/linux/${APP_NAME}-${VERSION}-x86_64.AppImage"
ARCH=x86_64 "$APPIMAGE_TOOL" "$APPDIR" "$OUTPUT" 2>&1
chmod +x "$OUTPUT"

echo ""
echo "==> AppImage generated: $OUTPUT"
echo "    ./$OUTPUT"
echo ""
echo "    Full desktop integration (optional):"
echo "    sudo apt install appimagelauncher"
