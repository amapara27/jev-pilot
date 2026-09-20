#!/bin/zsh
set -euo pipefail

project_root="${0:A:h:h}"
configuration="${CONFIGURATION:-release}"
app_dir="$project_root/.build/Jev Pilot.app"
contents_dir="$app_dir/Contents"

cd "$project_root"
swift build -c "$configuration" --product JevPilot

mkdir -p "$contents_dir/MacOS" "$contents_dir/Resources"
cp "$project_root/.build/$configuration/JevPilot" "$contents_dir/MacOS/JevPilot"
cp "$project_root/Resources/Info.plist" "$contents_dir/Info.plist"
codesign --force --deep --sign - "$app_dir"

print "$app_dir"
