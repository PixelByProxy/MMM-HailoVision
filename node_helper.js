/* MagicMirror²
 * Node Helper: MMM-HailoVision
 *
 * Responsibilities:
 *   1. Mount a REST endpoint on MagicMirror's shared Express app. The Hailo
 *      Python pipeline POSTs { action, face, confidence } to it.
 *   2. Resolve each (action, face) pair against the configured action map and
 *      either run a shell command and/or ask the frontend to broadcast a
 *      MagicMirror notification.
 *   3. Optionally launch (and supervise) the Hailo Python pipeline so the whole
 *      system runs as a single MagicMirror application.
 */
const NodeHelper = require("node_helper");
const Log = require("logger");
const { exec, execSync, spawn } = require("child_process");
const path = require("path");

// The Python pipeline sets its process title to this (setproctitle /
// MAGIC_MIRROR_APP_TITLE in defines.py). We match on it to reap stale
// instances that a previous, uncleanly-stopped MagicMirror left holding the
// Hailo device.
const HAILO_APP_PROCTITLE = "Hailo Magic Mirror App";

// How long to wait before relaunching the pipeline after it exits.
const HAILO_RESTART_DELAY_MS = 5000;

// Allowed values for the config's `cameraInputMode`. Only whitelisted values
// are ever interpolated into the launch command (it runs through bash), so
// config can select the camera source but not inject arbitrary arguments.
const CAMERA_INPUT_MODES = ["usb", "rpi"];

module.exports = NodeHelper.create({
  start() {
    this.config = null;
    this.routeMounted = false;
    this.hailoProcess = null;
    this.stopping = false;
    // Reap the detached pipeline on every teardown path so it can't be
    // orphaned and keep holding the (single) Hailo device.
    //
    // The "exit" event covers a normal/graceful process exit, but it does NOT
    // fire on SIGTERM/SIGINT/SIGHUP — their default action terminates the
    // process without running exit handlers. That is exactly how the pipeline
    // got orphaned: MagicMirror is almost always stopped with SIGTERM, so node
    // died without ever reaping its detached child. Catch those signals too.
    // (SIGKILL of the host process itself cannot be caught — nothing can; the
    // pre-launch reap in launchHailoApp() is the safety net for that.)
    process.once("exit", () => this.killPipeline("SIGKILL"));
    for (const sig of ["SIGTERM", "SIGINT", "SIGHUP"]) {
      process.once(sig, () => {
        this.stopping = true;
        this.killPipeline("SIGKILL");
        process.exit(0);
      });
    }
    Log.info(`Starting node_helper for: ${this.name}`);
  },

  socketNotificationReceived(notification, payload) {
    if (notification === "HAILO_CONFIG") {
      // The frontend can be reloaded; only act on the first config (or refresh
      // the stored config without double-mounting the route / relaunching).
      const firstConfig = this.config === null;
      this.config = payload;
      if (firstConfig) {
        this.mountRoute();
        if (this.config.launchHailoApp) {
          this.launchHailoApp();
        }
      }
      this.sendSocketNotification("HAILO_STATUS", { status: "ready" });
    }
  },

  // ---- REST endpoint ----------------------------------------------------
  mountRoute() {
    if (this.routeMounted) {
      return;
    }
    const apiPath = "/" + String(this.config.apiPath || "MMM-HailoVision/action").replace(/^\/+/, "");

    // MagicMirror exposes a shared Express instance as this.expressApp.
    // body-parser/express.json is already registered by MagicMirror core, but
    // register a tolerant JSON parser scoped to our route just in case.
    const express = require("express");
    this.expressApp.use(apiPath, express.json());

    this.expressApp.post(apiPath, (req, res) => {
      this.handleActionRequest(req, res);
    });

    // Simple health check so the Python side can verify connectivity.
    this.expressApp.get(apiPath, (req, res) => {
      res.json({ ok: true, module: this.name });
    });

    this.routeMounted = true;
    Log.info(`${this.name}: REST endpoint mounted at POST ${apiPath}`);
  },

  handleActionRequest(req, res) {
    const body = req.body || {};

    // Optional shared-secret check.
    if (this.config.apiToken) {
      const provided = req.get("X-Hailo-Token") || body.token;
      if (provided !== this.config.apiToken) {
        Log.warn(`${this.name}: rejected request with invalid token`);
        return res.status(401).json({ ok: false, error: "invalid token" });
      }
    }

    const action = body.action;
    const face = body.face || "*";
    if (!action) {
      return res.status(400).json({ ok: false, error: "missing 'action'" });
    }

    const handler = this.resolveHandler(action, face);
    if (!handler) {
      Log.info(`${this.name}: no handler configured for action='${action}' face='${face}'`);
      return res.json({ ok: true, matched: false, action, face });
    }

    Log.info(`${this.name}: action='${action}' face='${face}' -> ${JSON.stringify(handler)}`);

    // Tell the frontend to broadcast a notification (if configured).
    if (handler.notification) {
      this.sendSocketNotification("HAILO_ACTION", {
        action,
        face,
        confidence: body.confidence,
        notification: handler.notification,
        payload: handler.payload || {}
      });
    }

    // Run a shell command (if configured).
    if (handler.shell) {
      this.runShell(handler.shell, action, face);
    }

    return res.json({ ok: true, matched: true, action, face });
  },

  // Resolve actions[action][face], falling back to actions[action]["*"].
  resolveHandler(action, face) {
    const actions = this.config.actions || {};
    const forAction = actions[action];
    if (!forAction) {
      return null;
    }
    if (Object.prototype.hasOwnProperty.call(forAction, face)) {
      return forAction[face];
    }
    if (Object.prototype.hasOwnProperty.call(forAction, "*")) {
      return forAction["*"];
    }
    return null;
  },

  runShell(command, action, face) {
    const env = Object.assign({}, process.env, {
      HAILO_ACTION: action,
      HAILO_FACE: face
    });
    exec(command, { env }, (error, stdout, stderr) => {
      if (error) {
        Log.error(`${this.name}: shell command failed: ${error.message}`);
        return;
      }
      if (stdout) Log.info(`${this.name}: shell stdout: ${stdout.trim()}`);
      if (stderr) Log.warn(`${this.name}: shell stderr: ${stderr.trim()}`);
    });
  },

  // ---- Hailo pipeline launcher -----------------------------------------
  buildApiUrl() {
    // The Python pipeline POSTs back to this module. MagicMirror serves on
    // address/port from its own config; default to localhost:8080.
    const apiPath = String(this.config.apiPath || "MMM-HailoVision/action").replace(/^\/+/, "");
    const port = process.env.MM_PORT || 8080;
    return `http://localhost:${port}/${apiPath}`;
  },

  launchHailoApp() {
    if (this.hailoProcess) {
      return;
    }
    // A pipeline launched detached (below) survives if MagicMirror is killed
    // without a clean node_helper teardown (e.g. SIGKILL, crash, or a deploy
    // that only stops Electron). The orphan keeps holding the single Hailo
    // device, so the next launch dies with HAILO_OUT_OF_PHYSICAL_DEVICES.
    // Reap any such stale instance before spawning a fresh one.
    this.reapStalePipelines(() => this.spawnHailoApp());
  },

  // SIGTERM then (after a grace period) SIGKILL any process whose title matches
  // the Hailo pipeline, then invoke `done`. Best-effort: pkill is absent on
  // some systems, and "no matches" is the normal, healthy case.
  reapStalePipelines(done) {
    const needle = JSON.stringify(HAILO_APP_PROCTITLE); // shell-quote (has spaces)
    exec(`pkill -TERM -f ${needle}`, (err) => {
      // pkill exits 1 when nothing matched — that's the expected happy path.
      if (!err) {
        Log.warn(`${this.name}: reaped a stale Hailo pipeline (SIGTERM)`);
      }
      setTimeout(() => {
        exec(`pkill -KILL -f ${needle}`, () => done());
      }, 2000);
    });
  },

  // Whether `setpriv --pdeathsig` is available (util-linux; present on Linux,
  // absent on macOS). Memoized — the answer can't change at runtime.
  hasSetpriv() {
    if (this._hasSetpriv === undefined) {
      try {
        execSync("command -v setpriv", { stdio: "ignore" });
        this._hasSetpriv = true;
      } catch (_) {
        this._hasSetpriv = false;
      }
    }
    return this._hasSetpriv;
  },

  // Assemble the bash arguments for the pipeline launch.
  //
  // The pipeline runs through bash so setup_env.sh runs first: it activates
  // the venv_hailo_apps virtualenv (setproctitle, GStreamer bindings, etc.),
  // prepends the repo to PYTHONPATH, and loads Hailo env vars from
  // /usr/local/hailo/resources/.env. The pipeline must run once in
  // "--mode train" to populate the face vector DB from existing images, THEN
  // start the headless run. Both are chained in one command: train runs to
  // completion, and on success `exec` replaces the shell with the long-running
  // headless pipeline (so node_helper still supervises a single process).
  //
  // The only user-configurable piece is `cameraInputMode` ("usb" | "rpi" |
  // unset), which selects the camera via --input on the headless run. Unset
  // (or any non-whitelisted value) omits --input, so the pipeline falls back
  // to its bundled test video.
  buildHailoArgs() {
    let input = "";
    const mode = this.config.cameraInputMode;
    if (mode) {
      if (CAMERA_INPUT_MODES.includes(mode)) {
        input = ` --input ${mode}`;
      } else {
        Log.warn(
          `${this.name}: ignoring invalid cameraInputMode '${mode}' (allowed: ${CAMERA_INPUT_MODES.join(", ")})`
        );
      }
    }
    return [
      "-c",
      "source setup_env.sh && " +
        "python -u hailo_apps/python/pipeline_apps/magic_mirror/magic_mirror.py --mode train && " +
        `exec python -u hailo_apps/python/pipeline_apps/magic_mirror/magic_mirror.py --headless${input}`
    ];
  },

  spawnHailoApp() {
    if (this.hailoProcess) {
      return;
    }
    const command = "bash";
    const args = this.buildHailoArgs();
    // The bundled backend (where setup_env.sh and the script live) is at
    // <module>/hailo. Resolve it against THIS module's dir, not MagicMirror's
    // process cwd (the MM root) — a non-existent cwd makes spawn() fail with a
    // misleading "<cmd> ENOENT".
    const cwd = path.resolve(__dirname, "hailo");

    const env = Object.assign({}, process.env, {
      HAILO_MAGIC_MIRROR_ENABLED: "true",
      HAILO_MAGIC_MIRROR_API_URL: this.buildApiUrl()
    });
    if (this.config.apiToken) {
      env.HAILO_MAGIC_MIRROR_API_TOKEN = this.config.apiToken;
    }
    // Where the pipeline reads face-training images from. Passed as an env
    // var (not a shell argument), so arbitrary paths are safe to forward.
    if (this.config.trainingDir) {
      env.HAILO_MAGIC_MIRROR_TRAIN_DIR = String(this.config.trainingDir);
    }

    // Tie the pipeline's lifetime to this host process at the kernel level:
    // setpriv sets PR_SET_PDEATHSIG so the OS sends the pipeline SIGTERM the
    // moment its parent (the MagicMirror/Electron process that owns this
    // node_helper) dies — by ANY means. This is what actually prevents orphans:
    // Electron swallows SIGTERM before our JS signal handlers run, and SIGKILL
    // can't be caught at all, so an in-process handler is not enough. The
    // setting is preserved across the wrapper's exec into the long-running
    // python. Falls back to a plain spawn where setpriv is unavailable (the
    // pre-launch reap above is the safety net then).
    let spawnCmd = command;
    let spawnArgs = args;
    if (this.hasSetpriv()) {
      spawnCmd = "setpriv";
      spawnArgs = ["--pdeathsig", "TERM", command, ...args];
    }

    Log.info(`${this.name}: launching Hailo pipeline: ${command} ${args.join(" ")} (cwd=${cwd})`);

    // detached:true puts the child in its own process group so we can later
    // signal the whole group (the bash wrapper, python, and any GStreamer
    // children) at once and never orphan the preview window.
    const child = spawn(spawnCmd, spawnArgs, { cwd, env, detached: true });
    this.hailoProcess = child;
    this.sendSocketNotification("HAILO_STATUS", { status: "pipeline running" });

    child.stdout.on("data", (data) => Log.info(`[hailo] ${data.toString().trim()}`));
    child.stderr.on("data", (data) => Log.warn(`[hailo] ${data.toString().trim()}`));

    child.on("exit", (code, signal) => {
      Log.warn(`${this.name}: Hailo pipeline exited (code=${code} signal=${signal})`);
      this.hailoProcess = null;
      this.sendSocketNotification("HAILO_STATUS", { status: "pipeline stopped" });
      if (!this.stopping) {
        Log.info(`${this.name}: restarting Hailo pipeline in ${HAILO_RESTART_DELAY_MS}ms`);
        setTimeout(() => this.launchHailoApp(), HAILO_RESTART_DELAY_MS);
      }
    });

    child.on("error", (err) => {
      Log.error(`${this.name}: failed to start Hailo pipeline: ${err.message}`);
      this.hailoProcess = null;
      this.sendSocketNotification("HAILO_STATUS", { status: "pipeline error" });
    });
  },

  // Terminate the pipeline and everything it spawned. Signals the whole
  // process group (negative pid) so the bash wrapper, python, and GStreamer
  // all go down together; falls back to the child alone if the group is gone.
  killPipeline(signal) {
    const child = this.hailoProcess;
    if (!child || !child.pid) {
      return;
    }
    this.hailoProcess = null;
    const sig = signal || "SIGTERM";
    try {
      process.kill(-child.pid, sig);
    } catch (err) {
      try {
        child.kill(sig);
      } catch (_) {
        /* already gone */
      }
    }
  },

  stop() {
    // Called by MagicMirror on shutdown.
    this.stopping = true;
    Log.info(`${this.name}: stopping Hailo pipeline`);
    this.killPipeline("SIGTERM");
  }
});
