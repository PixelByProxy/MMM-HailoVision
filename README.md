# MMM-HailoVision

## Description

This [MagicMirror²][mm] module lets your mirror react to what its camera sees,
using Hailo-accelerated face recognition and gesture detection. Swipe left or
right in front of your mirror to change pages, or have it greet you by name
when it recognizes your face!

For each `(action, face)` pair you decide what happens: broadcast a
MagicMirror notification (to control other modules) and/or run a shell command
on the host. Supported actions out of the box: `face_recognition`,
`swipe_left`, `swipe_right`. You can add any custom action key — it just has
to match the `action` string the pipeline sends.

## Prerequisites

This module requires:

- **MagicMirror²** — an existing [MagicMirror²][mm] installation to add this
  module to.
- **A Raspberry Pi with Hailo-powered AI hardware** — either the Raspberry Pi
  AI HAT+ or the Raspberry Pi AI HAT+ 2. See the
  [Raspberry Pi AI documentation][rpi-ai] for more information.
- **A camera** — either a Raspberry Pi camera or any other USB camera. See the
  [Raspberry Pi camera documentation][rpi-camera] for more information on the
  Raspberry Pi camera options.

## Installation

In your terminal, go to your MagicMirror's module directory:

```bash
cd ~/MagicMirror/modules
```

Clone this repository (the repo root **is** the module) and run the setup
script:

```bash
git clone https://github.com/PixelByProxy/MMM-HailoVision.git
cd MMM-HailoVision
./setup.sh
```

The setup script requires root privileges and will prompt for your password
via `sudo` if needed.

Then add the module block to your MagicMirror config (see
[Configuration](#configuration)).

## Update

Go to the module's directory inside your MagicMirror's module directory and
pull the latest version:

```bash
cd ~/MagicMirror/modules/MMM-HailoVision
git pull
./setup.sh
```

## Configuration

To use this module, add a configuration to the modules array in the
`config/config.js` file.

*Note*: You can find a complete, copy-paste configuration example in
[config.example.js](config.example.js).

```js
    {
        module: "MMM-HailoVision",
        position: "bottom_left",
        config: {
            cameraInputMode: "rpi",
            actions: {
                swipe_left:  { "*": { notification: "PAGE_INCREMENT" } },
                swipe_right: { "*": { notification: "PAGE_DECREMENT" } },
                face_recognition: {
                    Anna: { notification: "SHOW_ALERT", payload: { title: "Hailo Vision", message: "Hi Anna!", timer: 4000 } },
                    "*":  { shell: "echo recognized $HAILO_FACE" }
                }
            }
        }
    },
```

### Configuration options

| Option             | Type     | Default Value | Description |
| ------------------ | -------- | ------------- | ----------- |
| `actionCooldownMs` | `int`    | `500`         | Minimum milliseconds between two executions of the same `(action, face)` handler; repeated events inside the window are acknowledged but not acted on. Set to `0` to disable rate limiting. |
| `actions`          | `object` | see below     | `action → face → handler` map. See [The `actions` map](#the-actions-map). |
| `apiToken`         | `String` | `""`          | Optional shared secret. When set, requests must send it in the `X-Hailo-Token` header. |
| `cameraInputMode`  | `String` | `""`          | Camera source for the pipeline: `"usb"` (USB webcam, auto-detected) or `"rpi"` (Raspberry Pi camera). Empty/undefined omits `--input`, so the pipeline uses its bundled test video. |
| `launchHailoApp`   | `bool`   | `true`        | Launch the Hailo Python pipeline on startup. |
| `minFaceConfidence` | `float` | `0.7`         | Minimum face-recognition confidence (0–1) required before the pipeline sends a `face_recognition` action. Forwarded as the `HAILO_MAGIC_MIRROR_MIN_FACE_CONFIDENCE` env var. |
| `minGestureConfidence` | `float` | `0.7`      | Minimum person-detection confidence (0–1) required before the pipeline sends a swipe gesture. Forwarded as the `HAILO_MAGIC_MIRROR_MIN_GESTURE_CONFIDENCE` env var. |
| `showStatus`       | `bool`   | `false`       | Show a small status line in the module region. |
| `trainingDir`      | `String` | `""`          | Directory of face-training images (one subfolder per person). Forwarded to the pipeline as the `HAILO_MAGIC_MIRROR_TRAIN_DIR` env var. Empty uses the bundled default inside the module. |

### The `actions` map

```js
actions: {
  swipe_left:  { "*": { notification: "PAGE_INCREMENT" } },
  swipe_right: { "*": { notification: "PAGE_DECREMENT" } },
  face_recognition: {
    Anna:    { notification: "SHOW_ALERT", payload: { title: "Hailo Vision", message: "Hi Anna!", timer: 4000 } },
    "*":     { shell: "echo recognized $HAILO_FACE" }
  }
}
```

- The first key is the **action**.
- The second key is the **face** (the recognized person label), or `"*"` to
  match any face. An exact face match wins; otherwise `"*"` is used.
- Each **handler** may define:
  - `notification` (+ optional `payload`): a MagicMirror notification that is
    broadcast via `sendNotification`, so other modules can react (e.g.
    [MMM-pages][pages] listens for `PAGE_INCREMENT` / `PAGE_DECREMENT`).
  - `shell`: a host command. `$HAILO_ACTION` and `$HAILO_FACE` are available in
    its environment.

## Notifications

This module does not handle any incoming notifications. The notifications it
sends out are entirely defined by your `actions` configuration: whenever a
handler with a `notification` key matches an incoming event, that notification
is broadcast to all modules via `sendNotification`, with the configured
`payload` (if any).

For example, with the configuration above, a `swipe_left` event broadcasts
`PAGE_INCREMENT`, which [MMM-pages][pages] uses to switch pages.

## How it connects to the Python pipeline

When `launchHailoApp` is `true`, the module injects these environment variables
into the pipeline process so it knows where to POST:

```
HAILO_MAGIC_MIRROR_ENABLED=true
HAILO_MAGIC_MIRROR_API_URL=http://localhost:<MagicMirror port>/MMM-HailoVision/action
HAILO_MAGIC_MIRROR_API_TOKEN=<apiToken, if set>
HAILO_MAGIC_MIRROR_MIN_GESTURE_CONFIDENCE=<minGestureConfidence>
HAILO_MAGIC_MIRROR_MIN_FACE_CONFIDENCE=<minFaceConfidence>
```

If you'd rather run the pipeline yourself, leave `launchHailoApp: false` and set
those variables manually (see the magic_mirror app README).

## REST API

`POST /MMM-HailoVision/action`

```json
{ "action": "swipe_left", "face": "Alice", "confidence": 0.92 }
```

Response: `{ "ok": true, "matched": true, "action": "...", "face": "..." }`.
`matched` is `false` when no handler is configured for that pair (still HTTP
200). A `GET` on the same path is a health check.

[mm]: https://github.com/MagicMirrorOrg/MagicMirror
[pages]: https://github.com/edward-shen/MMM-pages
[rpi-ai]: https://www.raspberrypi.com/documentation/computers/ai.html
[rpi-camera]: https://www.raspberrypi.com/documentation/accessories/camera.html
