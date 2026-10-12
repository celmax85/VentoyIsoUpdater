#!/usr/bin/env bash
# Builds a .rpm package for Fedora/openSUSE/RHEL/Rocky/Alma…
# Requires: rpm-build  ->  sudo dnf install rpm-build
# Usage: ./package_rpm.sh

set -e

if ! command -v rpmbuild &>/dev/null; then
    echo "ERROR: rpmbuild not found."
    echo "  Fedora/RHEL: sudo dnf install rpm-build"
    echo "  openSUSE   : sudo zypper install rpm-build"
    exit 1
fi

APP_NAME="VentoyIsoUpdater"
PKG_NAME="ventoy-iso-updater"
VERSION="${VERSION:-$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)}"   # overridable: VERSION=1.2.3 ./package_rpm.sh
RELEASE="1"
BINARY="dist/linux/$APP_NAME"
MAINTAINER="Maxence Baffet"
DESCRIPTION="Desktop GUI for managing Ventoy USB drives."

if [ ! -f "$BINARY" ]; then
    echo "ERROR: $BINARY not found. Run ./build.sh first."
    exit 1
fi

echo "==> Building the .rpm package..."

RPMBUILD_DIR="build/rpm/rpmbuild"
mkdir -p "$RPMBUILD_DIR"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

SRCDIR="build/rpm/${PKG_NAME}-${VERSION}"
rm -rf "$SRCDIR"
mkdir -p "$SRCDIR/usr/bin"
mkdir -p "$SRCDIR/usr/share/applications"
mkdir -p "$SRCDIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$SRCDIR/usr/share/pixmaps"

cp "$BINARY" "$SRCDIR/usr/bin/$APP_NAME"
chmod 755 "$SRCDIR/usr/bin/$APP_NAME"
cp "assets/icon.png" "$SRCDIR/usr/share/icons/hicolor/256x256/apps/$APP_NAME.png"
cp "assets/icon.png" "$SRCDIR/usr/share/pixmaps/$APP_NAME.png"

cat > "$SRCDIR/usr/share/applications/$APP_NAME.desktop" << EOF
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

tar -czf "$RPMBUILD_DIR/SOURCES/${PKG_NAME}-${VERSION}.tar.gz" \
    -C "build/rpm" "${PKG_NAME}-${VERSION}"

cat > "$RPMBUILD_DIR/SPECS/$PKG_NAME.spec" << EOF
Name:       $PKG_NAME
Version:    $VERSION
Release:    $RELEASE%{?dist}
Summary:    Desktop GUI for managing Ventoy USB drives
License:    MIT
Packager:   $MAINTAINER
Source0:    %{name}-%{version}.tar.gz

%description
$DESCRIPTION
VentoyIsoUpdater is a desktop GUI for managing Ventoy USB drives.
ISO update checks, downloads, theme management.

%prep
%setup -q

%install
mkdir -p %{buildroot}
cp -r usr %{buildroot}/

%post
gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database /usr/share/applications 2>/dev/null || true

%postun
gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database /usr/share/applications 2>/dev/null || true

%files
%attr(755, root, root) /usr/bin/$APP_NAME
/usr/share/applications/$APP_NAME.desktop
/usr/share/icons/hicolor/256x256/apps/$APP_NAME.png
/usr/share/pixmaps/$APP_NAME.png
EOF

rpmbuild --define "_topdir $(pwd)/$RPMBUILD_DIR" \
         -bb "$RPMBUILD_DIR/SPECS/$PKG_NAME.spec"

find "$RPMBUILD_DIR/RPMS" -name "*.rpm" -exec cp {} dist/linux/ \;
RPM_FILE=$(find dist/linux/ -maxdepth 1 -name "${PKG_NAME}*.rpm" | head -1)

echo ""
echo "==> .rpm package generated: $RPM_FILE"
echo "    sudo dnf install $RPM_FILE"
echo "    Uninstall: sudo dnf remove $PKG_NAME"
