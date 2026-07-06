/* Example MagicMirror² config entry for MMM-HailoVision.
 *
 * Copy the module block below into the `modules` array of your
 * MagicMirror `config/config.js`.
 *
 * The (action, face) map decides what happens for each recognized event.
 * Each handler can broadcast a MagicMirror notification (to control other
 * modules) and/or run a shell command on the host.
 */
{
  module: "MMM-HailoVision",
  position: "bottom_left",
  config: {
    // REST endpoint the Hailo Python pipeline POSTs to. Reachable at
    //   http://<mirror-host>:<mm-port>/MMM-HailoVision/action
    apiPath: "MMM-HailoVision/action",
    // Optional shared secret (also set HAILO_MAGIC_MIRROR_API_TOKEN on the
    // Python side, which the launcher injects automatically). Sent in the
    // "X-Hailo-Token" request header.
    apiToken: "",
    // Minimum ms between two executions of the same (action, face) handler;
    // 0 disables rate limiting.
    actionCooldownMs: 500,

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
        Alice: {
          notification: "SHOW_ALERT",
          payload: { title: "Welcome back", message: "Hi Alice!", timer: 4000 }
        },
        Anna: {
          notification: "SHOW_ALERT",
          payload: { title: "Welcome back", message: "Hi Anna!", timer: 4000 }
        },
        Bob: {
          notification: "SHOW_ALERT",
          payload: { title: "Welcome back", message: "Hi Bob!", timer: 4000 }
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

    launchHailoApp: true,

    // Camera source for the pipeline: "usb" (USB webcam, auto-detected) or
    // "rpi" (Raspberry Pi camera). Leave empty/undefined to omit --input, in
    // which case the pipeline uses its bundled test video.
    cameraInputMode: "",

    // Directory with face-training images (one subfolder per person, e.g.
    // trainingDir: "/home/pi/faces" containing faces/Alice/*.jpg). Leave
    // empty to use the bundled default inside the module.
    trainingDir: "",

    showStatus: true
  }
}
