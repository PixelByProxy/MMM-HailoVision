#!/bin/bash
# Cleanup installation artifacts from hailo-apps

set -euo pipefail

sudo rm -rf hailo/resources hailo_apps.egg-info/ hailo/venv_hailo_apps/ hailort.log 
sudo rm -rf /usr/local/hailo/resources/
