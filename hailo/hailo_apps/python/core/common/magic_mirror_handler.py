# region imports
# Standard library imports
import json
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

    def __init__(self, api_url, api_token=""):
        if not api_url:
            raise ValueError("MagicMirror API URL must be provided.")
        self.api_url = api_url
        self.api_token = api_token

    def send_action(self, action, face=None, confidence=None):
        """POST a recognized action/face to the MagicMirror module.

        Returns the parsed JSON response on success, or ``None`` on failure.
        Failures are logged but never raised, so the pipeline keeps running
        even when MagicMirror is unreachable.
        """
        if not action:
            raise ValueError("action must not be empty.")

        payload = {"action": action, "face": face if face else "*"}
        if confidence is not None:
            payload["confidence"] = round(float(confidence), 3)
        if self.api_token:
            payload["token"] = self.api_token

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
