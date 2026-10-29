#!/bin/sh
# Build rexm_<version>_all.deb. Run on Linux from the folder holding rexm.py, sony.py, bt_linux.py, ui/.
# The window uses the system's WebKitGTK and Python, so only pure-Python libraries are bundled.
set -e
VERSION=${1:-1.0.0}
OUT=$(pwd)/dist
ROOT=$(mktemp -d)/rexm_${VERSION}_all
APP=$ROOT/opt/rexm

mkdir -p "$APP/ui" "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/256x256/apps" "$OUT"
cp rexm.py sony.py bt_linux.py "$APP/"
cp ui/index.html "$APP/ui/"

# pure-Python dependencies not packaged by Debian/Ubuntu/Mint
python3 -m pip install --quiet --no-deps --no-compile --target "$APP/vendor" \
    pywebview pystray proxy_tools bottle typing_extensions
rm -rf "$APP/vendor/bin" "$APP/vendor"/*.dist-info/RECORD
cp LICENSE THIRD_PARTY_NOTICES.md "$APP/" 2>/dev/null || true

# app icon, drawn by rexm itself
PYTHONPATH="$APP/vendor" python3 -c "
import sys; sys.path.insert(0, '$APP')
from rexm import make_icon; make_icon().save('$ROOT/usr/share/icons/hicolor/256x256/apps/rexm.png')"
find "$APP" -name __pycache__ -prune -exec rm -rf {} +

cat > "$ROOT/usr/bin/rexm" <<'EOF'
#!/bin/sh
PYTHONPATH=/opt/rexm/vendor exec python3 /opt/rexm/rexm.py "$@"
EOF
chmod 755 "$ROOT/usr/bin/rexm"

cat > "$ROOT/usr/share/applications/rexm.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=reXm
Comment=Control Sony WH/WF-1000XM headphones
Exec=rexm
Icon=rexm
Terminal=false
Categories=AudioVideo;Audio;Settings;
Keywords=sony;headphones;bluetooth;noise cancelling;eq;
EOF

cat > "$ROOT/DEBIAN/control" <<EOF
Package: rexm
Version: $VERSION
Architecture: all
Maintainer: kurushimu <fallen4500x@outlook.com>
Depends: python3 (>= 3.10), python3-gi, gir1.2-webkit2-4.1, python3-pil, python3-xlib, python3-six, bluez, gir1.2-ayatanaappindicator3-0.1
Section: sound
Priority: optional
Description: Control app for Sony WH/WF-1000XM headphones
 Noise cancelling, ambient sound, EQ, Speak-to-Chat, profiles, battery and
 more, over Sony's Bluetooth control channel. Tray icon with quick modes.
EOF

dpkg-deb --build --root-owner-group "$ROOT" "$OUT/rexm_${VERSION}_all.deb"
echo "built $OUT/rexm_${VERSION}_all.deb"
