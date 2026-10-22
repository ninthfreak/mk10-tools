#!/bin/sh
# Build the plugin for Linux and Windows and zip it for OpenDeck's "Install from file".
# Windows cross-build needs the x86_64-pc-windows-gnu Rust target and MinGW (x86_64-w64-mingw32-gcc).
set -e
cd "$(dirname "$0")"
cargo build --release
cargo build --release --target x86_64-pc-windows-gnu
out=dist/local.opendeck-mk10.sdPlugin
rm -rf dist && mkdir -p "$out/assets"
cp target/release/opendeck-mk10 "$out/opendeck-mk10-linux"
cp target/x86_64-pc-windows-gnu/release/opendeck-mk10.exe "$out/opendeck-mk10-win.exe"
cp manifest.json "$out/"
cp assets/icon.png "$out/assets/"
cp packaging/60-opendeck-mk10.rules dist/
chmod 755 "$out/opendeck-mk10-linux"
(cd dist && zip -qr opendeck-mk10.plugin.zip local.opendeck-mk10.sdPlugin)
echo "Built dist/opendeck-mk10.plugin.zip"
