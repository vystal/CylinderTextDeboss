#!/bin/sh
# Installs (or updates) the Cylinder Text add-in for Fusion on macOS:
#
#     curl -fsSL https://raw.githubusercontent.com/vystal/CylinderTextDeboss/main/install.sh | sh
#
# Downloads the latest version and puts it in Fusion's add-ins folder,
# replacing any copy already there.
set -e
addins="$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns"
target="$addins/CylinderTextDeboss"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl -fsSL https://github.com/vystal/CylinderTextDeboss/archive/refs/heads/main.tar.gz | tar -xz -C "$work"
mkdir -p "$addins"
rm -rf "$target"
mv "$work/CylinderTextDeboss-main" "$target"
echo "Installed to $target"
echo "In Fusion: Utilities > Add-Ins (Shift+S), select CylinderTextDeboss and click Run."
