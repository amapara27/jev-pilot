#!/bin/zsh
# Launch the bundled app with an explicit dotenv path, never a key on the command line.
set -euo pipefail

project_root="${0:A:h:h}"
app_dir="$project_root/.build/Jev Pilot.app"
env_file="$project_root/.env"

if [[ ! -f "$env_file" ]]; then
  print -u2 "Missing $env_file. Create it with TYPESAFE_API_KEY=... first."
  exit 1
fi
if [[ ! -x "$app_dir/Contents/MacOS/JevPilot" ]]; then
  print -u2 "Missing app bundle. Run ./scripts/build-app.sh first."
  exit 1
fi

# -n ensures the launch argument reaches a new process; quit any old Jev Pilot first.
open -n "$app_dir" --args --jev-dev-env-file "$env_file"
