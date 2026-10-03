#!/bin/zsh
# Builds the executable and packages it as a signed macOS app bundle.
set -euo pipefail

project_root="${0:A:h:h}"
configuration="${CONFIGURATION:-release}"
signing_identity="${JEV_CODESIGN_IDENTITY:--}"
app_dir="$project_root/.build/Jev Pilot.app"
contents_dir="$app_dir/Contents"

cd "$project_root"
build_options=()
if [[ "${JEV_DISABLE_SWIFTPM_SANDBOX:-0}" == "1" ]]; then
  build_options+=(--disable-sandbox)
fi
swift build -c "$configuration" --product JevPilot "${build_options[@]}"

mkdir -p "$contents_dir/MacOS" "$contents_dir/Resources"
cp "$project_root/.build/$configuration/JevPilot" "$contents_dir/MacOS/JevPilot"
cp "$project_root/Resources/Info.plist" "$contents_dir/Info.plist"
codesign --force --deep --sign "$signing_identity" "$app_dir"

print "$app_dir"
