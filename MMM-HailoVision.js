/* MagicMirror²
 * Module: MMM-HailoVision
 *
 * Bridges the Hailo "Magic Mirror" face-recognition / gesture pipeline into
 * MagicMirror². The Python pipeline POSTs recognized actions (face_recognition,
 * swipe_left, swipe_right) together with the recognized face to this module's
 * REST endpoint (served by node_helper). For each (action, face) pair the user
 * configures what should happen inside MagicMirror: broadcast a module
 * notification and/or run a shell command.
 *
 * The node_helper can also launch the Hailo Python pipeline on startup so the
 * whole system runs as a single MagicMirror application.
 */
Module.register("MMM-HailoVision", {
  // Default module configuration.
  defaults: {
    // Minimum milliseconds between two executions of the same (action, face)
    // handler. Repeated events inside the window are acknowledged but not
    // acted on. 0 disables rate limiting.
    actionCooldownMs: 500,

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
          payload: { title: "Hailo Vision", message: "Swipe Left!", timer: 4000 }
        }
      },
      swipe_right: {
        "*": {
          notification: "SHOW_ALERT",
          payload: { title: "Hailo Vision", message: "Swipe Right!", timer: 4000 }
        }
      },
      face_recognition: {
        "*": {
          notification: "SHOW_ALERT",
          payload: { title: "Hailo Vision", message: "Face detected" }
        }
      }
    },

    // Optional shared secret. If set, incoming requests must send the same
    // value in the "X-Hailo-Token" header.
    apiToken: "",

    // Camera source for the pipeline: "usb" (USB webcam, auto-detected) or
    // "rpi" (Raspberry Pi camera). Leave empty/undefined to omit --input, in
    // which case the pipeline uses its bundled test video.
    cameraInputMode: "",

    // ---- Hailo pipeline launcher ----
    // When enabled, node_helper spawns the Python pipeline on startup. The
    // launch command itself is fixed inside node_helper and is not
    // configurable.
    launchHailoApp: true,

    // Minimum face-recognition confidence (0–1) required before the pipeline
    // calls this module's API with a face_recognition action. Forwarded as
    // HAILO_MAGIC_MIRROR_MIN_FACE_CONFIDENCE.
    minFaceConfidence: 0.8,

    // Minimum person-detection confidence (0–1) required before the pipeline
    // calls this module's API with a swipe gesture. Forwarded as
    // HAILO_MAGIC_MIRROR_MIN_GESTURE_CONFIDENCE.
    minGestureConfidence: 0.8,

    // Show a small status line in the module region.
    showStatus: false,

    // Directory with face-training images (one subfolder per person, e.g.
    // trainingDir: "/home/pi/faces" containing faces/Alice/*.jpg). Leave
    // empty to use the bundled default inside the module.
    trainingDir: ""
  },

  start() {
    this.status = "starting";
    this.lastEvent = null;
    Log.info(`Starting module: ${this.name}`);
    // Hand the config to node_helper so it can mount the REST route and,
    // optionally, launch the Hailo pipeline. Include MagicMirror's actual
    // port (from the global config) so the helper can build the callback URL
    // the pipeline POSTs to — the helper itself has no access to it.
    const mmPort = typeof config !== "undefined" && config.port ? config.port : undefined;
    this.sendSocketNotification("HAILO_CONFIG", Object.assign({}, this.config, { mmPort }));
  },

  getStyles() {
    return ["MMM-HailoVision.css"];
  },

  // Notifications coming back from node_helper.
  socketNotificationReceived(notification, payload) {
    if (notification === "HAILO_STATUS") {
      this.status = payload.status;
      if (this.config.showStatus) {
        this.updateDom();
      }
    } else if (notification === "HAILO_ACTION") {
      // node_helper resolved an (action, face) pair and asked us to broadcast
      // a notification into the rest of MagicMirror.
      this.lastEvent = payload;
      if (payload.notification) {
        this.sendNotification(payload.notification, payload.payload || {});
      }
      if (this.config.showStatus) {
        this.updateDom();
      }
    }
  },

  getDom() {
    const wrapper = document.createElement("div");
    wrapper.className = "hailo-magic-mirror";

    if (!this.config.showStatus) {
      return wrapper;
    }

    const statusEl = document.createElement("div");
    statusEl.className = "hailo-status";
    statusEl.textContent = `Hailo: ${this.status}`;
    wrapper.appendChild(statusEl);

    if (this.lastEvent) {
      const eventEl = document.createElement("div");
      eventEl.className = "hailo-event dimmed small";
      // textContent, never innerHTML: `face` comes from the network (any
      // string matches the "*" handler) and must not be parsed as HTML.
      const face = this.lastEvent.face || "?";
      eventEl.textContent = `${this.lastEvent.action} → ${face}`;
      wrapper.appendChild(eventEl);
    }

    return wrapper;
  }
});
