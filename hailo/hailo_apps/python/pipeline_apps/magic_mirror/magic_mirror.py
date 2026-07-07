# region imports
# Standard library imports
import time
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
from hailo_apps.python.core.common.bounded_lru import BoundedLruDict
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
# Consecutive classifications a NEW person label must persist on the SAME face
# track before we accept it. Recognition confidence flickers around the
# threshold (Alice <-> Unknown), and every accepted switch fires a
# face_recognition action - so unstable labels must not get through. Debounce
# is per face track: with two people in frame, each face accumulates its own
# count and neither resets the other's.
FACE_STABLE_FRAMES = 10
# A recognized label fires face_recognition only when the person stabilizes
# after being UNSEEN for at least this long. The tracker keeps lost tracks for
# just a few frames, so occlusion or walking past re-issues track IDs for the
# same person every few seconds - without this refractory window, each new
# track would re-fire the person's action (re-greeting spam).
FACE_REFIRE_ABSENCE_SECONDS = 30
# Cap on per-track state entries (gesture history, recognized labels). Track
# IDs increase monotonically for as long as the pipeline runs, so unbounded
# dicts keyed by them grow forever on an always-on mirror.
GESTURE_STATE_MAX_ENTRIES = 100

# COCO pose keypoint indices as produced by the pose-estimation postprocess.
COCO_KEYPOINTS = {
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
# endregion


class user_callbacks_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.frame = None
        self.latest_track_id = -1
        self.gesture_tracks = BoundedLruDict(GESTURE_STATE_MAX_ENTRIES)
        self.latest_gesture_frame = BoundedLruDict(GESTURE_STATE_MAX_ENTRIES)
        # Label gestures are tagged with: the most recently stabilized face.
        self.current_person_label = None
        # Per face-track recognition state: track_id -> stable label, and
        # track_id -> (challenger label, consecutive sightings).
        self.track_person_labels = BoundedLruDict(GESTURE_STATE_MAX_ENTRIES)
        self.track_person_candidates = BoundedLruDict(GESTURE_STATE_MAX_ENTRIES)
        # label -> monotonic time the label was last confirmed on any track;
        # drives the FACE_REFIRE_ABSENCE_SECONDS refractory window.
        self.label_last_seen = BoundedLruDict(GESTURE_STATE_MAX_ENTRIES)

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

    def update_current_person(self, track_id, person_label):
        """
        Debounced, per face track: accept a label for this track only after it
        has been seen FACE_STABLE_FRAMES consecutive times, so confidence
        flicker around the recognition threshold doesn't re-fire actions.
        Tracks are independent - with two people in frame, each face
        accumulates its own count and neither resets the other's (a single
        global challenger would starve every person after the first).

        Returns True when a person is newly recognized: their label
        stabilized on a track AND they were unseen on every track for at
        least FACE_REFIRE_ABSENCE_SECONDS. The absence window is what stops
        tracker ID churn (occlusion re-issues track IDs for the same person
        within seconds) from re-firing the action for someone who never left.
        Only a fired (newly recognized) label becomes
        ``current_person_label`` (used to tag gestures) and resets gesture
        state so a swipe can't span two people; a suppressed re-stabilization
        is a pure presence-refresh with no side effects.
        """
        if person_label == self.track_person_labels.get(track_id):
            # Label re-confirmed for this track; any challenger was flicker.
            # Refresh presence so a track-ID churn right after this doesn't
            # look like the person returning from an absence.
            self.label_last_seen[person_label] = time.monotonic()
            self.track_person_candidates.pop(track_id, None)
            return False
        candidate = self.track_person_candidates.get(track_id)
        if candidate is None or candidate[0] != person_label:
            self.track_person_candidates[track_id] = (person_label, 1)
            return False
        sightings = candidate[1] + 1
        if sightings < FACE_STABLE_FRAMES:
            self.track_person_candidates[track_id] = (person_label, sightings)
            return False
        self.track_person_candidates.pop(track_id, None)
        self.track_person_labels[track_id] = person_label
        now = time.monotonic()
        last_seen = self.label_last_seen.get(person_label)
        self.label_last_seen[person_label] = now
        if last_seen is not None and (now - last_seen) < FACE_REFIRE_ABSENCE_SECONDS:
            # Same person re-stabilized after tracker ID churn: they never
            # left the scene, so don't retag gestures with their label or
            # wipe someone else's in-progress gesture history.
            return False
        if person_label != self.current_person_label:
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
    fmt, width, height = get_caps_from_pad(pad)
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
                    person_changed = user_data.update_current_person(track_id, person_label)
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
            if not landmarks or not fmt or not width or not height:
                continue

            bbox = detection.get_bbox()
            points = landmarks[0].get_points()
            for wrist_name in ("left_wrist", "right_wrist"):
                point = points[COCO_KEYPOINTS[wrist_name]]
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
