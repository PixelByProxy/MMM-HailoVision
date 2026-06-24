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
from hailo_apps.python.core.common.discord_handler import DiscordHandler
from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer_efficient
from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.common.telegram_handler import TelegramHandler
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
TELEGRAM_ENABLED = False
TELEGRAM_TOKEN = ''
TELEGRAM_CHAT_ID = ''
DISCORD_ENABLED = get_env_bool("HAILO_DISCORD_ENABLED", False)
DISCORD_TOKEN = get_env_str("HAILO_DISCORD_TOKEN")
DISCORD_CHANNEL_ID = get_env_str("HAILO_DISCORD_CHANNEL_ID")
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

        # Telegram settings as instance attributes
        self.telegram_enabled = TELEGRAM_ENABLED
        self.telegram_token = TELEGRAM_TOKEN
        self.telegram_chat_id = TELEGRAM_CHAT_ID

        # Discord settings as instance attributes
        self.discord_enabled = DISCORD_ENABLED
        self.discord_token = DISCORD_TOKEN
        self.discord_channel_id = DISCORD_CHANNEL_ID

        # Initialize TelegramHandler if Telegram is enabled
        self.telegram_handler = None
        if self.telegram_enabled and self.telegram_token and self.telegram_chat_id:
            self.telegram_handler = TelegramHandler(self.telegram_token, self.telegram_chat_id)

        # Initialize DiscordHandler if Discord is enabled
        self.discord_handler = None
        if self.discord_enabled and self.discord_token and self.discord_channel_id:
            self.discord_handler = DiscordHandler(self.discord_token, self.discord_channel_id)

    def send_notification(self, name, global_id, confidence, frame):
        """
        Check if notification handlers are enabled and send notifications.
        """
        if (
            self.telegram_enabled
            and self.telegram_handler
            and self.telegram_handler.should_send_notification(global_id)
        ):
            self.telegram_handler.send_notification(name, global_id, confidence, frame)

        if (
            self.discord_enabled
            and self.discord_handler
            and self.discord_handler.should_send_notification(global_id)
        ):
            self.discord_handler.send_notification(name, global_id, confidence, frame)
    # endregion

    def update_current_person(self, person_label):
        """
        Reset gesture state when face recognition switches to a different person.
        """
        if person_label == self.current_person_label:
            return
        self.current_person_label = person_label
        self.gesture_tracks.clear()
        self.latest_gesture_frame.clear()

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
    frame = None
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
                    user_data.update_current_person(person_label)
                    if person_label == 'Unknown':
                        string_to_print += 'Unknown person detected'
                    else:
                        string_to_print += f'Person recognition: {person_label} (Confidence: {classification.get_confidence():.1f})'
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
                if user_data.telegram_enabled or user_data.discord_enabled:
                    if frame is None:
                        frame = get_numpy_from_buffer_efficient(buffer, format, width, height)
                    user_data.send_notification(
                        name=gesture_name,
                        global_id=f"gesture:{track_id}:{gesture_name}",
                        confidence=confidence,
                        frame=frame,
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
