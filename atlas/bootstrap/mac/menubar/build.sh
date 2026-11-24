#!/bin/zsh
# Build "ATLAS Menu.app" with swiftc (no Xcode project) and, with --install,
# copy it to ~/Applications and launch it.
#
#   ./build.sh             -> build/ATLAS Menu.app
#   ./build.sh --install   -> also ~/Applications/ATLAS Menu.app, relaunched
set -euo pipefail
here=${0:A:h}
cd "$here"

app="build/ATLAS Menu.app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

# Swift 5 language mode: the Swift 6 default buries AppKit delegate code in
# actor-isolation errors that change nothing at runtime here.
swiftc -O -swift-version 5 \
  -target "$(uname -m)-apple-macos13.0" \
  -framework Cocoa -framework WebKit -framework ServiceManagement -framework Carbon \
  -framework Speech -framework AVFoundation \
  -o "$app/Contents/MacOS/ATLASMenu" main.swift voice.swift voicepack.swift cmdtap.swift

cp Info.plist "$app/Contents/Info.plist"
printf 'APPL????' > "$app/Contents/PkgInfo"

# Ad-hoc signature, no hardened runtime, no sandbox: enough for macOS to
# show the microphone prompt and remember the answer. The signature changes
# with every rebuild, so macOS may ask for the microphone again after one.
codesign --force --sign - --identifier Miro.k9.atlasmenu "$app"
codesign --verify --verbose=2 "$app"

if [[ "${1:-}" == "--install" ]]; then
  dest="$HOME/Applications/ATLAS Menu.app"
  mkdir -p "$HOME/Applications"
  pkill -x ATLASMenu 2>/dev/null || true
  sleep 0.5
  rm -rf "$dest"
  ditto "$app" "$dest"
  open "$dest"
  echo "installed and launched: $dest"
fi
