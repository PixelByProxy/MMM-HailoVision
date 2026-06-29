/* MagicMirror²
 * Module: MMM-HailoMagicMirror
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
Module.register("MMM-HailoMagicMirror", {
  // Default module configuration.
  defaults: {
    // ---- REST API ----
    // Path the REST endpoint is mounted on. The full URL the Python pipeline
    // must POST to is:  http://<mirror-host>:<mm-port>/<apiPath>
    apiPath: "MMM-HailoMagicMirror/action",
    // Optional shared secret. If set, incoming requests must send the same
    // value in the "X-Hailo-Token" header (or a "token" body field).
    apiToken: "",

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
        "*": { notification: "PAGE_INCREMENT" }
      },
      swipe_right: {
        "*": { notification: "PAGE_DECREMENT" }
      },
      face_recognition: {
        "*": {
          notification: "SHOW_ALERT",
          payload: { title: "Magic Mirror", message: "Face detected" }
        }
      }
    },

    // ---- Hailo pipeline launcher ----
    // When enabled, node_helper spawns the Python pipeline on startup.
    launchHailoApp: false,
    hailoApp: {
      command: "python",
      // Args are passed verbatim to the command.
      args: [
        "hailo_apps/python/pipeline_apps/magic_mirror/magic_mirror.py",
        "--input",
        "usb"
      ],
      // Working directory the command is run from (defaults to the repo root,
      // resolved by node_helper if left empty).
      cwd: "",
      // Extra environment variables merged into the child process env.
      // node_helper automatically injects HAILO_MAGIC_MIRROR_ENABLED and
      // HAILO_MAGIC_MIRROR_API_URL so the pipeline can reach this module.
      env: {},
      // Restart the pipeline automatically if it exits.
      autoRestart: true,
      restartDelayMs: 5000
    },

    // Show a small status line in the module region.
    showStatus: true
  },

  start() {
    this.status = "starting";
    this.lastEvent = null;
    Log.info(`Starting module: ${this.name}`);
    // Hand the config to node_helper so it can mount the REST route and,
    // optionally, launch the Hailo pipeline.
    this.sendSocketNotification("HAILO_CONFIG", this.config);
  },

  getStyles() {
    return ["MMM-HailoMagicMirror.css"];
  },

  // Notifications coming back from node_helper.
  socketNotificationReceived(notification, payload) {
    if (notification === "HAILO_STATUS") {
      this.status = payload.status;
      this.updateDom();
    } else if (notification === "HAILO_ACTION") {
      // node_helper resolved an (action, face) pair and asked us to broadcast
      // a notification into the rest of MagicMirror.
      this.lastEvent = payload;
      if (payload.notification) {
        this.sendNotification(payload.notification, payload.payload || {});
      }
      this.updateDom();
    }
  },

  getDom() {
    const wrapper = document.createElement("div");
    wrapper.className = "hailo-magic-mirror";

    if (!this.config.showStatus) {
      wrapper.style.display = "none";
      return wrapper;
    }

    const statusEl = document.createElement("div");
    statusEl.className = "hailo-status";
    statusEl.innerHTML = `Hailo: ${this.status}`;
    wrapper.appendChild(statusEl);

    if (this.lastEvent) {
      const eventEl = document.createElement("div");
      eventEl.className = "hailo-event dimmed small";
      const face = this.lastEvent.face || "?";
      eventEl.innerHTML = `${this.lastEvent.action} &rarr; ${face}`;
      wrapper.appendChild(eventEl);
    }

    return wrapper;
  }
});
