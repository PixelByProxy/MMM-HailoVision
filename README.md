# MMM-HailoVision

A [MagicMirror²](https://magicmirror.builders/) module that bridges the Hailo
**Magic Mirror** face-recognition / gesture pipeline (this repo's
`hailo/hailo_apps/python/pipeline_apps/magic_mirror`) into MagicMirror.

This repository root **is** the MagicMirror module; the Hailo pipeline backend it
drives lives in the [`hailo/`](hailo/) subdirectory.

It does three things:

1. **REST API** — exposes an HTTP endpoint on MagicMirror's web server. The
   Hailo Python pipeline POSTs recognized events `{ action, face, confidence }`
   to it.
2. **Configurable per-face actions** — for each `(action, face)` pair you decide
   what happens: broadcast a MagicMirror notification (to control other modules)
   and/or run a shell command on the host.
3. **Pipeline launcher** — optionally spawns and supervises the Hailo Python
   pipeline on MagicMirror startup, so the whole system runs as a single app.

Supported actions out of the box: `face_recognition`, `swipe_left`,
`swipe_right`. You can add any custom action key — it just has to match the
`action` string the pipeline sends.

## Installation

Clone this repository into your MagicMirror `modules/` directory (the repo
root **is** the module) and install its one dependency:

```bash
cd ~/MagicMirror/modules
git clone https://github.com/PixelByProxy/MMM-HailoVision.git
cd MMM-HailoVision
npm install        # installs express
```

Then either add the module block to your MagicMirror config by hand (see
below), or let the deploy script (re)inject it and restart MagicMirror:

```bash
scripts/deploy_magic_mirror.sh
```

The module runs in place — the deploy script only manages the config block
and the MagicMirror restart; it does not copy files.

## Configuration

Add a module block to your MagicMirror `config/config.js`. See
[config.example.js](config.example.js) for a complete, copy-paste example.

| Option | Default | Description |
|---|---|---|
| `apiPath` | `MMM-HailoVision/action` | Path the REST endpoint is mounted on. |
| `apiToken` | `""` | Optional shared secret. When set, requests must send it in the `X-Hailo-Token` header. |
| `actionCooldownMs` | `500` | Minimum milliseconds between two executions of the same `(action, face)` handler; repeated events inside the window are acknowledged but not acted on. `0` disables rate limiting. |
| `actions` | see below | `action → face → handler` map. |
| `launchHailoApp` | `true` | Launch the Hailo Python pipeline on startup. |
| `cameraInputMode` | `""` | Camera source for the pipeline: `"usb"` (USB webcam, auto-detected) or `"rpi"` (Raspberry Pi camera). Empty/undefined omits `--input`, so the pipeline uses its bundled test video. |
| `trainingDir` | `""` | Directory of face-training images (one subfolder per person). Forwarded to the pipeline as the `HAILO_MAGIC_MIRROR_TRAIN_DIR` env var. Empty uses the bundled default inside the module. |
| `showStatus` | `true` | Show a small status line in the module region. |

### The `actions` map

```js
actions: {
  swipe_left:  { "*": { notification: "PAGE_INCREMENT" } },
  swipe_right: { "*": { notification: "PAGE_DECREMENT" } },
  face_recognition: {
    Anna:    { notification: "SHOW_ALERT", payload: { title: "Welcome Anna!" } },
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
    [MMM-pages](https://github.com/edward-shen/MMM-pages) listens for
    `PAGE_INCREMENT` / `PAGE_DECREMENT`).
  - `shell`: a host command. `$HAILO_ACTION` and `$HAILO_FACE` are available in
    its environment.

## How it connects to the Python pipeline

When `launchHailoApp` is `true`, the module injects these environment variables
into the pipeline process so it knows where to POST:

```
HAILO_MAGIC_MIRROR_ENABLED=true
HAILO_MAGIC_MIRROR_API_URL=http://localhost:<MagicMirror port>/MMM-HailoVision/action
HAILO_MAGIC_MIRROR_API_TOKEN=<apiToken, if set>
```

If you'd rather run the pipeline yourself, leave `launchHailoApp: false` and set
those variables manually (see the magic_mirror app README).

## REST API

`POST /<apiPath>`

```json
{ "action": "swipe_left", "face": "Ryan", "confidence": 0.92 }
```

Response: `{ "ok": true, "matched": true, "action": "...", "face": "..." }`.
`matched` is `false` when no handler is configured for that pair (still HTTP
200). A `GET` on the same path is a health check.
