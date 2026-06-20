# Magic Mirror

Real-time **face recognition** and **gesture detection** for Hailo accelerators, built on GStreamer.

This repository is a slimmed-down distribution of [Hailo-Apps](https://github.com/hailo-ai/hailo-apps) containing only the **Magic Mirror** application and the core framework it needs to run.

## Supported Platforms and Devices
| Platforms | Accelerators |
|---|---|
| ![Raspberry Pi](https://img.shields.io/badge/Raspberry-Pi%205-red?logo=raspberrypi&logoColor=white) ![Ubuntu](https://img.shields.io/badge/Ubuntu-x86__64-E95420?logo=ubuntu&logoColor=white) | ![Hailo-8](https://img.shields.io/badge/Hailo-8-00A4EF?logoColor=white) ![Hailo-8L](https://img.shields.io/badge/Hailo-8L-00A4EF?logoColor=white) |

## What it does

Magic Mirror recognizes known people in a live video stream and detects basic
gestures (`swipe_left`, `swipe_right`) using pose estimation. It runs three models
on the Hailo device — face detection (SCRFD), face recognition (ArcFace), and pose
estimation (YOLOv8-pose) — and stores face embeddings in a local [LanceDB](https://lancedb.github.io/)
database. When a person (known or unknown) or a gesture is detected, an optional
notification can be sent via Telegram or Discord.

See the [application guide](hailo_apps/python/pipeline_apps/magic_mirror/README.md) for full details on training, the database API, the FiftyOne web interface, and tuning parameters.

## Requirements

Install these packages **before** running `install.sh`. Download them from the [Hailo Developer Zone](https://hailo.ai/developer-zone/).

| Package | Type | Required For |
|---|---|---|
| HailoRT PCIe Driver | .deb | All apps |
| HailoRT | .deb | All apps |
| TAPPAS Core | .deb | GStreamer pipeline |
| HailoRT Python Binding | .whl | Python app |
| TAPPAS Core Python Binding | .whl | GStreamer pipeline |

## Quick Start

### Install

```bash
git clone <this-repo-url>
cd hailo-apps-magic-mirror
sudo ./install.sh
source setup_env.sh           # activate the Python virtual environment
export DISPLAY=:0             # only needed when running headless
```

> This build ships the prebuilt postprocess `.so` libraries, so installation skips
> C++ compilation automatically.

### Run

```bash
hailo-magic-mirror --mode train          # populate the database from training images
hailo-magic-mirror --input usb           # run face recognition + gestures from a USB camera
hailo-magic-mirror --mode delete         # clear the database
```

Run `hailo-magic-mirror --help` for all options (input source, mirror flags, model
overrides, logging, etc.). You can also run the app directly with
`python hailo_apps/python/pipeline_apps/magic_mirror/magic_mirror.py`.

## Notifications (optional)

Discord notifications are configured via environment variables:

```bash
export HAILO_DISCORD_ENABLED=true
export HAILO_DISCORD_TOKEN="YOUR_BOT_TOKEN"
export HAILO_DISCORD_CHANNEL_ID="YOUR_CHANNEL_ID"
hailo-magic-mirror --input usb
```

Telegram is also supported (requires the `telebot` package). If no notification
provider is configured, notifications are simply disabled. See the
[application guide](hailo_apps/python/pipeline_apps/magic_mirror/README.md) for details.

## Support

💬 [Hailo Community Forum](https://community.hailo.ai/)

**License:** MIT - see [LICENSE](LICENSE)
