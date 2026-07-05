# MMM-HailoVision

A [MagicMirror²](https://magicmirror.builders/) module that bridges the Hailo
**Magic Mirror** face-recognition / gesture pipeline (this repo's
`hailo/hailo_apps/python/pipeline_apps/magic_mirror`) into MagicMirror.

This repository root **is** the MagicMirror module; the Hailo pipeline backend it
drives lives in the [`hailo/`](hailo/) subdirectory.

It does three things:

1. **REST API** — exposes an HTTP endpoint on MagicMirror's web server. The
   Hailo Python pipeline POSTs recognized events `{ action, face, confidence }`
   to it (the same way the pipeline already talks to Discord/Telegram).
2. **Configurable per-face actions** — for each `(action, face)` pair you decide
   what happens: broadcast a MagicMirror notification (to control other modules)
   and/or run a shell command on the host.
3. **Pipeline launcher** — optionally spawns and supervises the Hailo Python
   pipeline on MagicMirror startup, so the whole system runs as a single app.

Supported actions out of the box: `face_recognition`, `swipe_left`,
`swipe_right`. You can add any custom action key — it just has to match the
`action` string the pipeline sends.

## Installation

**Option A — deploy script (recommended).** Copies just the module files into
your MagicMirror install and (re)injects the config block:

```bash
/home/pi/Documents/repos/hailo-apps-magic-mirror/scripts/deploy_magic_mirror.sh
```

**Option B — symlink.** This repo root is the module, so symlink it directly:

```bash
cd ~/MagicMirror/modules
ln -s /home/pi/Documents/repos/hailo-apps-magic-mirror MMM-HailoVision
cd MMM-HailoVision
npm install        # installs express
```

## Configuration

Add a module block to your MagicMirror `config/config.js`. See
[config.example.js](config.example.js) for a complete, copy-paste example.

| Option | Default | Description |
|---|---|---|
| `apiPath` | `MMM-HailoVision/action` | Path the REST endpoint is mounted on. |
| `apiToken` | `""` | Optional shared secret. When set, requests must send it in the `X-Hailo-Token` header (or a `token` body field). |
| `actions` | see below | `action → face → handler` map. |
| `launchHailoApp` | `true` | Launch the Hailo Python pipeline on startup. |
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
HAILO_MAGIC_MIRROR_API_URL=http://localhost:8080/MMM-HailoVision/action
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
