/* Example MagicMirror² config entry for MMM-HailoMagicMirror.
 *
 * Copy the module block below into the `modules` array of your
 * MagicMirror `config/config.js`.
 *
 * The (action, face) map decides what happens for each recognized event.
 * Each handler can broadcast a MagicMirror notification (to control other
 * modules) and/or run a shell command on the host.
 */
{
  module: "MMM-HailoMagicMirror",
  position: "bottom_left",
  config: {
    // REST endpoint the Hailo Python pipeline POSTs to. Reachable at
    //   http://<mirror-host>:<mm-port>/MMM-HailoMagicMirror/action
    apiPath: "MMM-HailoMagicMirror/action",
    // Optional shared secret (also set HAILO_MAGIC_MIRROR_API_TOKEN on the
    // Python side, which the launcher injects automatically).
    apiToken: "",

    // action -> face -> { notification, payload, shell }
    actions: {
      // Swipe gestures page through MagicMirror's pages (works with the
      // MMM-pages module). "*" matches any recognized person.
      swipe_left: {
        "*": { notification: "PAGE_INCREMENT" }
      },
      swipe_right: {
        "*": { notification: "PAGE_DECREMENT" }
      },

      // Face recognition: greet known people, run a command for a specific
      // person, and fall back for anyone else.
      face_recognition: {
        Ryan: {
          notification: "SHOW_ALERT",
          payload: { title: "Welcome back", message: "Hi Ryan!", timer: 4000 }
        },
        Unknown: {
          notification: "SHOW_ALERT",
          payload: { title: "Magic Mirror", message: "Unknown person", timer: 3000 }
        },
        "*": {
          // Example: run any host command when a face is recognized.
          shell: "echo \"Hailo recognized $HAILO_FACE\""
        }
      }
    },

    // Launch the Hailo Python pipeline on MagicMirror startup so everything
    // runs as a single application.
    launchHailoApp: true,
    hailoApp: {
      // Launch through bash so setup_env.sh runs first: it activates the
      // venv_hailo_apps virtualenv (which has setproctitle, GStreamer bindings,
      // etc.), prepends the repo to PYTHONPATH, and loads Hailo env vars from
      // /usr/local/hailo/resources/.env. Plain "python" has none of that.
      // No --input => bundled test video. For a USB webcam append "--input usb".
      // --headless drops the OpenCV/GStreamer preview window so the pipeline
      // runs without a display (recommended here; MagicMirror is the display).
      command: "bash",
      args: [
        "-c",
        "source setup_env.sh && exec python -u hailo_apps/python/pipeline_apps/magic_mirror/magic_mirror.py --headless"
      ],
      // Absolute path to the hailo-apps repo root. Required when this module is
      // deployed into MagicMirror's modules/ dir (default cwd would otherwise be
      // <MagicMirror>/modules); also where setup_env.sh and the script live.
      cwd: "/home/pi/Documents/repos/hailo-apps-magic-mirror",
      env: {},
      autoRestart: true,
      restartDelayMs: 5000
    },

    showStatus: true
  }
}
