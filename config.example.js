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
  position: "bottom_right",
  config: {
    // ---- Action mapping ----
    // actions[action][face] = { notification, payload, shell }
    //   action: "face_recognition" | "swipe_left" | "swipe_right" (or custom)
    //   face:   recognized person label, or "*" to match any face.
    // Each handler may define:
    //   notification: a MagicMirror notification to broadcast (sendNotification)
    //   payload:      payload object for that notification
    //   shell:        a shell command string to execute on the host
    actions: {
      swipe_left: {
        "*": {
          notification: "SHOW_ALERT",
          payload: { title: "Magic Mirror", message: "Swipe Left!", timer: 4000 }
        }
      },
      swipe_right: {
        "*": {
          notification: "SHOW_ALERT",
          payload: { title: "Hailo Vision", message: "Swipe Right!", timer: 4000 }
        }
      },
      face_recognition: {
        Alice: {
          notification: "SHOW_ALERT",
          payload: { title: "Hailo Vision", message: "Hi Alice!", timer: 4000 }
        },
        Anna: {
          notification: "SHOW_ALERT",
          payload: { title: "Hailo Vision", message: "Hi Anna!", timer: 4000 }
        },
        Bob: {
          notification: "SHOW_ALERT",
          payload: { title: "Hailo Vision", message: "Hi Bob!", timer: 4000 }
        },
        Unknown: {
          notification: "SHOW_ALERT",
          payload: { title: "Hailo Vision", message: "Unknown person", timer: 3000 }
        },
        "*": {
          // Example: run any host command when a face is recognized.
          shell: "echo \"Hailo Vision recognized $HAILO_FACE\""
        }
      }
    },

    // Camera source for the pipeline: "usb" (USB webcam, auto-detected) or
    // "rpi" (Raspberry Pi camera). Leave empty/undefined to omit --input, in
    // which case the pipeline uses its bundled test video.
    cameraInputMode: "",

    // Directory with face-training images (one subfolder per person, e.g.
    // trainingDir: "/home/pi/faces" containing faces/Alice/*.jpg). Leave
    // empty to use the bundled default inside the module.
    trainingDir: ""
  }
}
