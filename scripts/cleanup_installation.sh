#!/bin/bash
# Cleanup installation artifacts from hailo-apps

set -euo pipefail

# Delete by absolute path so the script works from any working directory —
# these are `rm -rf` targets, so cwd-relative paths are not acceptable.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

sudo rm -rf \
    "$REPO_ROOT/hailo/resources" \
    "$REPO_ROOT/hailo/hailo_apps.egg-info" \
    "$REPO_ROOT/hailo/venv_hailo_apps" \
    "$REPO_ROOT/hailort.log" \
    "$REPO_ROOT/hailo/hailort.log"
sudo rm -rf /usr/local/hailo/resources/
