#!/bin/zsh
# Explicitly run real Jev + native desktop effects, then leave a machine-readable artifact.
set -euo pipefail
project_root="${0:A:h:h}"
cd "$project_root"
if pgrep -x JevPilot >/dev/null; then
  print -u2 "Quit Jev Pilot before running the native E2E check."
  exit 1
else
  task_process_status=$?
  if [[ "$task_process_status" != "1" ]]; then
    print -u2 "Cannot check for a running instance. Run this test from an unrestricted Terminal."
    exit 1
  fi
fi
if [[ ! -f "$project_root/.env" ]]; then
  print -u2 "Create the ignored .env with TYPESAFE_API_KEY first."
  exit 1
fi
./scripts/build-app.sh
test_output=$(mktemp -d "$project_root/.build/e2e.XXXXXX")
print "Native desktop test: creates two test notes and a disposable Finder fixture; types pwd without Return."
print "Leave the desktop alone until the report is written: $test_output/report.json"
open -n "$project_root/.build/Jev Pilot.app" --args --jev-dev-env-file "$project_root/.env" --jev-e2e-output "$test_output"
for attempt in {1..190}; do
  if [[ -f "$test_output/report.json" ]]; then
    print "$test_output/report.json"
    if [[ "$(plutil -extract passed raw -o - "$test_output/report.json")" == "true" ]]; then
      print "Native E2E passed."
      exit 0
    fi
    print -u2 "Native E2E did not pass; inspect the report above."
    exit 1
  fi
  sleep 1
done
print -u2 "No report after 190 seconds. Check permission prompts/the app; test output: $test_output"
exit 1
