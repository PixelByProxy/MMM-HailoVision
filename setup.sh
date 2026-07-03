#!/bin/bash
# Setup script for MMM-HailoVision.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# install.sh requires root privileges.
if [ "$(id -u)" -eq 0 ]; then
    "$SCRIPT_DIR/hailo/install.sh"
else
    sudo "$SCRIPT_DIR/hailo/install.sh"
fi
