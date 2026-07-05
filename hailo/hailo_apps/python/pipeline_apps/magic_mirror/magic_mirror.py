# region imports
# Standard library imports
import datetime
from collections import deque
from datetime import datetime
import os
os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

# Third-party imports
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

# Local application-specific imports
import hailo
from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad
from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.common.magic_mirror_handler import MagicMirrorHandler
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class
from hailo_apps.python.pipeline_apps.magic_mirror.magic_mirror_pipeline import GStreamerMagicMirrorApp

hailo_logger = get_logger(__name__)
# endregion imports


def get_env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def get_env_str(name, default=""):
    return os.getenv(name, default).strip()


# region Constants
# MagicMirror² module integration (MMM-HailoVision REST API).
MAGIC_MIRROR_ENABLED = get_env_bool("HAILO_MAGIC_MIRROR_ENABLED", False)
MAGIC_MIRROR_API_URL = get_env_str("HAILO_MAGIC_MIRROR_API_URL")
MAGIC_MIRROR_API_TOKEN = get_env_str("HAILO_MAGIC_MIRROR_API_TOKEN")
GESTURE_HISTORY_LENGTH = 12
GESTURE_MIN_DELTA_RATIO = 0.35
GESTURE_MIN_DELTA_PIXELS = 80
GESTURE_MAX_VERTICAL_RATIO = 0.45
GESTURE_COOLDOWN_FRAMES = 45
# Number of samples averaged at each end of the window to reject single-frame keypoint jitter.
GESTURE_SMOOTHING_SAMPLES = 3
# Fraction of total horizontal travel that must be in the dominant direction for a clean swipe
# (rejects back-and-forth waves and noisy jitter that net out to a false direction).
GESTURE_DIRECTION_CONSISTENCY = 0.75
# endregion


class user_callbacks_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.frame = None
        self.latest_track_id = -1
        self.gesture_tracks = {}
        self.latest_gesture_frame = {}
        self.current_person_label = None

        # MagicMirror settings as instance attributes
        self.magic_mirror_enabled = MAGIC_MIRROR_ENABLED
        self.magic_mirror_api_url = MAGIC_MIRROR_API_URL
        self.magic_mirror_api_token = MAGIC_MIRROR_API_TOKEN

        # Initialize MagicMirrorHandler if MagicMirror integration is enabled
        self.magic_mirror_handler = None
        if self.magic_mirror_enabled and self.magic_mirror_api_url:
            self.magic_mirror_handler = MagicMirrorHandler(
                self.magic_mirror_api_url, self.magic_mirror_api_token
            )

    def send_magic_mirror_action(self, action, face=None, confidence=None):
        """Forward a recognized action/face to the MagicMirror module (if enabled)."""
        if self.magic_mirror_enabled and self.magic_mirror_handler:
            self.magic_mirror_handler.send_action(action=action, face=face, confidence=confidence)

    def update_current_person(self, person_label):
        """
        Reset gesture state when face recognition switches to a different person.

        Returns True when the recognized person changed (a new face), so the
        caller can forward a one-shot ``face_recognition`` action.
        """
        if person_label == self.current_person_label:
            return False
        self.current_person_label = person_label
        self.gesture_tracks.clear()
        self.latest_gesture_frame.clear()
        return True

    def update_gesture(self, track_id, wrist_name, x, y, bbox_width, bbox_height):
        """
        Track wrist movement and return a recognized gesture name when a swipe completes.

        Direction is reported from the person's perspective (mirror-style): "swipe_right"
        means the user moved their hand toward their own right. The camera feed is not
        horizontally flipped, so the user's right is toward decreasing image-x.
        """
        current_frame = self.get_count()
        state_key = (track_id, wrist_name)
        history = self.gesture_tracks.setdefault(state_key, deque(maxlen=GESTURE_HISTORY_LENGTH))
        history.append((current_frame, x, y))

        if len(history) < GESTURE_HISTORY_LENGTH:
            return None

        first_frame = history[0][0]
        # Drop stale windows where the wrist lingered (slow drift, not a deliberate swipe).
        if current_frame - first_frame > GESTURE_HISTORY_LENGTH * 2:
            history.clear()
            return None

        xs = [px for _, px, _ in history]
        ys = [py for _, _, py in history]

        # Average a few samples at each end so a single bad keypoint can't flip the result.
        k = min(GESTURE_SMOOTHING_SAMPLES, len(xs) // 2)
        start_x = sum(xs[:k]) / k
        end_x = sum(xs[-k:]) / k
        start_y = sum(ys[:k]) / k
        end_y = sum(ys[-k:]) / k
        horizontal_delta = end_x - start_x
        vertical_delta = abs(end_y - start_y)

        # Require the motion to be consistently one-directional so back-and-forth waves
        # and jitter don't register as a swipe.
        step_deltas = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        forward = sum(d for d in step_deltas if d > 0)
        backward = -sum(d for d in step_deltas if d < 0)
        total_travel = forward + backward
        consistency = max(forward, backward) / total_travel if total_travel else 0.0

        horizontal_threshold = max(GESTURE_MIN_DELTA_PIXELS, bbox_width * GESTURE_MIN_DELTA_RATIO)
        vertical_threshold = max(GESTURE_MIN_DELTA_PIXELS, bbox_height * GESTURE_MAX_VERTICAL_RATIO)

        if (
            abs(horizontal_delta) < horizontal_threshold
            or vertical_delta > vertical_threshold
            or consistency < GESTURE_DIRECTION_CONSISTENCY
        ):
            return None

        gesture_name = "swipe_right" if horizontal_delta < 0 else "swipe_left"
        cooldown_key = (track_id, gesture_name)
        last_gesture_frame = self.latest_gesture_frame.get(cooldown_key, -GESTURE_COOLDOWN_FRAMES)
        if current_frame - last_gesture_frame < GESTURE_COOLDOWN_FRAMES:
            history.clear()
            return None

        self.latest_gesture_frame[cooldown_key] = current_frame
        history.clear()
        return gesture_name


def app_callback(element, buffer, user_data):
    # Note: Frame counting is handled automatically by the framework wrapper
    if buffer is None:
        hailo_logger.warning("Received None buffer.")
        return
    pad = element.get_static_pad("src")
    format, width, height = get_caps_from_pad(pad)
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
    for detection in detections:
        label = detection.get_label()
        detection_confidence = detection.get_confidence()
        if label == "face":
            track_id = 0
            track = detection.get_objects_typed(hailo.HAILO_UNIQUE_ID)
            if len(track) > 0:
                track_id = track[0].get_id()
            string_to_print = f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]: Face detection ID: {track_id} (Confidence: {detection_confidence:.1f}), '
            classifications = detection.get_objects_typed(hailo.HAILO_CLASSIFICATION)
            if len(classifications) > 0:
                for classification in classifications:
                    person_label = classification.get_label()
                    person_changed = user_data.update_current_person(person_label)
                    if person_label == 'Unknown':
                        string_to_print += 'Unknown person detected'
                    else:
                        string_to_print += f'Person recognition: {person_label} (Confidence: {classification.get_confidence():.1f})'
                    # Forward a one-shot face_recognition action to MagicMirror
                    # whenever the recognized person changes.
                    if person_changed:
                        user_data.send_magic_mirror_action(
                            action="face_recognition",
                            face=person_label,
                            confidence=classification.get_confidence(),
                        )
                    if track_id > user_data.latest_track_id:
                        user_data.latest_track_id = track_id
                        print(string_to_print)
        elif label == "person":
            track_id = 0
            track = detection.get_objects_typed(hailo.HAILO_UNIQUE_ID)
            if len(track) > 0:
                track_id = track[0].get_id()

            landmarks = detection.get_objects_typed(hailo.HAILO_LANDMARKS)
            if not landmarks or not format or not width or not height:
                continue

            bbox = detection.get_bbox()
            points = landmarks[0].get_points()
            keypoints = get_keypoints()
            for wrist_name in ("left_wrist", "right_wrist"):
                point = points[keypoints[wrist_name]]
                x = int((point.x() * bbox.width() + bbox.xmin()) * width)
                y = int((point.y() * bbox.height() + bbox.ymin()) * height)
                gesture_name = user_data.update_gesture(
                    track_id=track_id,
                    wrist_name=wrist_name,
                    x=x,
                    y=y,
                    bbox_width=bbox.width() * width,
                    bbox_height=bbox.height() * height,
                )
                if not gesture_name:
                    continue

                confidence = min(1.0, max(0.0, detection_confidence))
                print(
                    f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]: '
                    f'Gesture recognition: {gesture_name} from {wrist_name} '
                    f'for person ID: {track_id} (Confidence: {confidence:.1f})'
                )
                # Forward the swipe gesture to MagicMirror, tagged with the
                # currently recognized person so per-face actions can apply.
                user_data.send_magic_mirror_action(
                    action=gesture_name,
                    face=user_data.current_person_label,
                    confidence=confidence,
                )
    return


def get_keypoints():
    return {
        "nose": 0,
        "left_eye": 1,
        "right_eye": 2,
        "left_ear": 3,
        "right_ear": 4,
        "left_shoulder": 5,
        "right_shoulder": 6,
        "left_elbow": 7,
        "right_elbow": 8,
        "left_wrist": 9,
        "right_wrist": 10,
        "left_hip": 11,
        "right_hip": 12,
        "left_knee": 13,
        "right_knee": 14,
        "left_ankle": 15,
        "right_ankle": 16,
    }


def main():
    hailo_logger.info("Starting Magic Mirror App.")
    user_data = user_callbacks_class()
    pipeline = GStreamerMagicMirrorApp(app_callback, user_data)
    if pipeline.options_menu.mode == 'delete':
        pipeline.db_handler.clear_table()
        exit(0)
    elif pipeline.options_menu.mode == 'train':
        pipeline.run()
        exit(0)
    else:  # 'run' mode
        pipeline.run()


if __name__ == "__main__":
    main()
