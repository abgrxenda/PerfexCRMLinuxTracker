#!/usr/bin/env bash
#
# build-deb.sh
# Builds the otek-perfex-linux-tracker .deb from this directory's debian/
# packaging files. No compilation involved (plain Python) - this just
# drives dpkg-buildpackage and moves the result into dist/, matching the
# dist/ convention already used by PerfexCRMLibreOfficeExtension's .oxt.
#
# Usage: ./build-deb.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Checking build dependencies..."
MISSING=()
command -v dpkg-buildpackage >/dev/null 2>&1 || MISSING+=("dpkg-dev")
dpkg -s debhelper >/dev/null 2>&1 || MISSING+=("debhelper")

if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "Missing build tool(s): ${MISSING[*]}"
    echo "Install with:"
    echo "  sudo apt install ${MISSING[*]}"
    exit 1
fi

echo "==> Building otek-perfex-linux-tracker.deb (unsigned)..."
dpkg-buildpackage -us -uc -b

# dpkg-buildpackage drops its output one directory up from the source
# tree by convention (not inside debian/ or the project folder itself).
PARENT_DIR="$(cd .. && pwd)"
BUILT_DEB="$(ls -t "$PARENT_DIR"/otek-perfex-linux-tracker_*.deb 2>/dev/null | head -n1 || true)"

if [ -z "$BUILT_DEB" ]; then
    echo "Build finished but no .deb was found in $PARENT_DIR - check the output above."
    exit 1
fi

mkdir -p dist
mv -v "$BUILT_DEB" dist/
# dpkg-buildpackage also drops a .buildinfo/.changes next to the .deb, one
# directory above this project (never inside it) - sweep those into
# dist/ too rather than leaving stray files in the parent directory.
# Neither is needed for a plain local install (only for signing/
# uploading to a real archive), but dist/ is the right home for them.
for f in "$PARENT_DIR"/otek-perfex-linux-tracker_*.buildinfo "$PARENT_DIR"/otek-perfex-linux-tracker_*.changes; do
    [ -e "$f" ] && mv -v "$f" dist/
done

DEB_PATH="dist/$(basename "$BUILT_DEB")"

echo
echo "==> Built: $DEB_PATH"
echo
echo "==> Contents:"
dpkg-deb --contents "$DEB_PATH"
echo
echo "==> Install with:"
echo "  sudo apt install ./$DEB_PATH"
echo
echo "==> Optional tray icon support (Recommends, not required):"
echo "  sudo apt install gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1"
echo "  (plus the 'AppIndicator and KStatusNotifierItem Support' GNOME Shell extension"
echo "   from extensions.gnome.org, and window-calls-extended for focus tracking itself)"
