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
        position: "bottom_right",
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
| `emptyFrameSeconds` | `float` | `3.0`         | Seconds the frame must be *continuously* empty before the pipeline sends a `face_recognition` action with face `None` (nobody present at all — distinct from `Unknown`). Detection drops out for a frame or two routinely, so this dwell time is what stops the display flickering; raise it if the mirror goes idle while you are still standing there. Forwarded as the `HAILO_MAGIC_MIRROR_EMPTY_FRAME_SECONDS` env var. |
| `launchHailoApp`   | `bool`   | `true`        | Launch the Hailo Python pipeline on startup. |
| `minFaceConfidence` | `float` | `0.6`         | Minimum face-recognition confidence (0–1) required before the pipeline sends a `face_recognition` action — "which person is this". Forwarded as the `HAILO_MAGIC_MIRROR_MIN_FACE_CONFIDENCE` env var. |
| `minFaceDetectionConfidence` | `float` | `0.6` | Minimum face-*detection* confidence (0–1) before the pipeline tries to recognize a detection at all — "is this even a face". Partial faces, reflections and background objects arrive as low-confidence detections and otherwise recognize as `Unknown`, firing spurious `face_recognition` actions. Raise it if unknown-person events fire when nobody is there; lower it if people are missed at the edge of frame or in poor light. Forwarded as the `HAILO_MAGIC_MIRROR_MIN_FACE_DETECTION_CONFIDENCE` env var. |
| `minGestureConfidence` | `float` | `0.8`      | Minimum person-detection confidence (0–1) required before the pipeline sends a swipe gesture. Forwarded as the `HAILO_MAGIC_MIRROR_MIN_GESTURE_CONFIDENCE` env var. |
| `unknownStableSeconds` | `float` | `2.0`      | Seconds an unrecognized face must stay in frame before the pipeline sends a `face_recognition` action with face `Unknown`. During this window the pipeline re-checks the face against the gallery every few frames, so somebody whose first frame was bad gets recognized rather than announced as a stranger. Lower it for a snappier `Unknown` page; raise it if known people briefly flash the `Unknown` page as they walk up. Forwarded as the `HAILO_MAGIC_MIRROR_UNKNOWN_STABLE_SECONDS` env var. |
| `showStatus`       | `bool`   | `false`       | Show a small status line in the module region. |
| `trainingDir`      | `String` | `""`          | Directory of face-training images (one subfolder per person). Forwarded to the pipeline as the `HAILO_MAGIC_MIRROR_TRAIN_DIR` env var. Empty uses the bundled default inside the module. |

### The `actions` map

```js
actions: {
  swipe_left:  { "*": { notification: "PAGE_INCREMENT" } },
  swipe_right: { "*": { notification: "PAGE_DECREMENT" } },
  face_recognition: {
    Anna:    { notification: "SHOW_ALERT", payload: { title: "Hailo Vision", message: "Hi Anna!", timer: 4000 } },
    Unknown: { notification: "SHOW_ALERT", payload: { title: "Hailo Vision", message: "Unknown person", timer: 3000 } },
    None:    { notification: "PAGE_CHANGED", payload: 0 },
    "*":     { shell: "echo recognized $HAILO_FACE" }
  }
}
```

- The first key is the **action**.
- The second key is the **face** (the recognized person label), or `"*"` to
  match any face. An exact face match wins; otherwise `"*"` is used.
- Two face labels are **synthesized** by the pipeline rather than trained, and
  are worth handling separately:
  - `Unknown` — a face is in frame but matches nobody in the gallery.
    *Somebody is there, and I don't know who.*
  - `None` — nobody is in frame at all, after the frame has stayed empty for
    `emptyFrameSeconds`. *Nobody is there.* Use it to send the mirror back to
    an idle or default page when the room empties. Fires once per departure,
    not repeatedly while the room stays empty.
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
HAILO_MAGIC_MIRROR_MIN_FACE_DETECTION_CONFIDENCE=<minFaceDetectionConfidence>
HAILO_MAGIC_MIRROR_EMPTY_FRAME_SECONDS=<emptyFrameSeconds>
HAILO_MAGIC_MIRROR_UNKNOWN_STABLE_SECONDS=<unknownStableSeconds>
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
