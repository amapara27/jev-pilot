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

# Prevent two instances fighting over foreground activation and microphone ownership.
if pgrep -x JevPilot >/dev/null; then
  print -u2 "Quit the running Jev Pilot first, then launch again."
  exit 1
else
  task_process_status=$?
  if [[ "$task_process_status" != "1" ]]; then
    print -u2 "Cannot check for a running instance. Launch from an unrestricted Terminal."
    exit 1
  fi
fi
# -n ensures the explicit development argument reaches the fresh process.
open -n "$app_dir" --args --jev-dev-env-file "$env_file"
