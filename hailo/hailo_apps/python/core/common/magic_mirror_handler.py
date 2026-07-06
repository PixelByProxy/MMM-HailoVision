# region imports
# Standard library imports
import json
import queue
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hailo_apps.python.core.common.hailo_logger import get_logger

# endregion imports

hailo_logger = get_logger(__name__)


class MagicMirrorHandler:
    """Send recognized actions to the MMM-HailoVision MagicMirror² module.

    The MagicMirror module exposes a REST endpoint. For every recognized
    action (``face_recognition``, ``swipe_left``, ``swipe_right``, ...) the
    pipeline POSTs ``{action, face, confidence}`` to that endpoint, and the
    module runs whatever command/notification was configured for that
    (action, face) pair.

    Construct it from environment configuration and call :meth:`send_action`
    from the pipeline callback.
    """

    # Actions waiting to be POSTed. Bounded so a long MagicMirror outage
    # can't accumulate an unbounded (and stale) backlog of gestures.
    QUEUE_MAX_ACTIONS = 32

    def __init__(self, api_url, api_token=""):
        if not api_url:
            raise ValueError("MagicMirror API URL must be provided.")
        self.api_url = api_url
        self.api_token = api_token
        # send_action() is called from the GStreamer buffer callback, where a
        # synchronous HTTP round-trip (up to the full socket timeout when
        # MagicMirror is down) would stall video processing. Actions are
        # queued and POSTed by a single background daemon thread instead.
        self._queue = queue.Queue(maxsize=self.QUEUE_MAX_ACTIONS)
        self._sender = threading.Thread(
            target=self._send_loop, name="magic-mirror-sender", daemon=True
        )
        self._sender.start()

    def send_action(self, action, face=None, confidence=None):
        """Queue a recognized action/face for delivery to the MagicMirror module.

        Never blocks and never raises on delivery problems: failures (and a
        full queue) are logged, so the pipeline keeps running even when
        MagicMirror is unreachable.
        """
        if not action:
            raise ValueError("action must not be empty.")

        payload = {"action": action, "face": face if face else "*"}
        if confidence is not None:
            payload["confidence"] = round(float(confidence), 3)

        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            hailo_logger.warning(
                f"MagicMirror action queue full; dropping action '{action}'"
            )

    def _send_loop(self):
        while True:
            payload = self._queue.get()
            self._post(payload)
            self._queue.task_done()

    def _post(self, payload):
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "hailo-apps-magic-mirror-handler",
        }
        if self.api_token:
            headers["X-Hailo-Token"] = self.api_token

        request = Request(self.api_url, data=data, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else None
        except HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            hailo_logger.warning(f"MagicMirror action failed: HTTP {e.code} - {error_body}")
        except URLError as e:
            hailo_logger.warning(f"MagicMirror action failed: {e.reason!s}")
        except Exception as e:
            hailo_logger.warning(f"MagicMirror action failed: {e!s}")
        return None
