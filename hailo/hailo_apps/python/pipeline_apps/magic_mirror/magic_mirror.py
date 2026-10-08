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
from hailo_apps.python.core.common.db_handler import UNRECOGNIZED_FACE_LABEL
from hailo_apps.python.core.common.env_utils import get_env_bool, get_env_float, get_env_str
from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.common.magic_mirror_handler import MagicMirrorHandler
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class
from hailo_apps.python.pipeline_apps.magic_mirror.magic_mirror_pipeline import GStreamerMagicMirrorApp

hailo_logger = get_logger(__name__)
# endregion imports


# region Constants
# MagicMirror² module integration (MMM-HailoVision REST API).
MAGIC_MIRROR_ENABLED = get_env_bool("HAILO_MAGIC_MIRROR_ENABLED", False)
MAGIC_MIRROR_API_URL = get_env_str("HAILO_MAGIC_MIRROR_API_URL")
MAGIC_MIRROR_API_TOKEN = get_env_str("HAILO_MAGIC_MIRROR_API_TOKEN")
# Events below these confidences are logged locally but never forwarded to
# the MagicMirror API. Gestures gate on the person-detection confidence,
# face_recognition on the classification confidence.
MAGIC_MIRROR_MIN_GESTURE_CONFIDENCE = get_env_float(
    "HAILO_MAGIC_MIRROR_MIN_GESTURE_CONFIDENCE", 0.8
)
MAGIC_MIRROR_MIN_FACE_CONFIDENCE = get_env_float(
    "HAILO_MAGIC_MIRROR_MIN_FACE_CONFIDENCE", 0.8
)
# Face detections below this confidence don't count as somebody being present,
# for the same reason the recognition stage ignores them: partial faces and
# reflections would otherwise keep the mirror permanently occupied. Same env
# var the recognition callback gates on, so the two stages agree on what
# counts as a face.
MAGIC_MIRROR_MIN_FACE_DETECTION_CONFIDENCE = get_env_float(
    "HAILO_MAGIC_MIRROR_MIN_FACE_DETECTION_CONFIDENCE", 0.6
)
# Label reported when the frame is empty. Deliberately distinct from
# UNRECOGNIZED_FACE_LABEL ("Unknown"): that one means "somebody is here and I
# don't know who", this one means "nobody is here at all" - a mirror usually
# wants a different page for each.
NO_PERSON_LABEL = "None"
# How long the frame must be CONTINUOUSLY empty before falling back to
# NO_PERSON_LABEL. Detection drops out for a frame or two constantly (a turn of
# the head, motion blur, a passing shadow), so an instant fallback would
# flicker the display; this is the "they have actually left" dwell time.
EMPTY_FRAME_SECONDS = get_env_float("HAILO_MAGIC_MIRROR_EMPTY_FRAME_SECONDS", 3.0)
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
# A swipe must be a cross-body motion: the wrist has to end at least this
# fraction of the bbox width past the body midline (shoulder midpoint). Small
# same-side hand movements can clear the distance thresholds but never cross
# the midline, so they no longer register as swipes.
GESTURE_MIDLINE_MARGIN_RATIO = 0.08
# A swipe only counts while the person is standing in front of the mirror, not
# while walking in or out of frame. Walking translates the whole body - and the
# wrist with it - across the image, which clears the travel and midline
# thresholds even though the arm never moved relative to the torso. So the
# torso has to stay roughly put for the whole gesture window: the body midline
# may drift by at most this fraction of the person's shoulder width.
#
# Shoulder width - not bbox width - is the scale reference on purpose. The
# person bbox includes the arms, so it narrows sharply during the very swipe
# being measured (arm out, then hand across the chest); normalizing by it made
# the gate tightest exactly when a real swipe was finishing. Shoulder width
# only tracks how far away the person is standing.
GESTURE_MAX_BODY_DRIFT_RATIO = 0.6
# Fallback shoulder-width estimate (as a fraction of bbox width) for frames
# where the shoulder keypoints land on top of each other.
GESTURE_SHOULDER_WIDTH_FALLBACK_RATIO = 0.35
# Consecutive classifications a NEW person label must persist on the SAME face
# track before we accept it. Recognition confidence flickers around the
# threshold (Alice <-> Bob), and every accepted switch fires a
# face_recognition action - so unstable labels must not get through. Debounce
# is per face track: with two people in frame, each face accumulates its own
# count and neither resets the other's.
FACE_STABLE_FRAMES = 10
# How long an unrecognized face must stick around before the mirror announces
# a stranger. "Unknown" is not an identity - it is the recognition stage
# failing to match an embedding - so it needs far more evidence than a name.
#
# Measured in SECONDS, and tracked across face tracks rather than within one,
# because the face tracker re-issues IDs roughly once a second (observed: 8 new
# track IDs in 6.3s with one person standing still). A per-track frame counter
# long enough to be meaningful therefore never completes - each new track ID
# resets it - which is why the earlier frame-based version meant "Unknown"
# could not fire at all. Elapsed time is immune to that churn.
#
# The duration must give somebody who IS in the gallery, but whose first frame
# was unusable, enough fresh attempts to be recognized before being called a
# stranger. The recognition callback re-searches a still-unrecognized face
# every unknown_recheck_frames (5 frames with the shipped
# face_recon_algo_params.json), so 2s at 30fps is about a dozen attempts. On
# real runs a misread person matched on the very first retry.
FACE_UNKNOWN_STABLE_SECONDS = get_env_float(
    "HAILO_MAGIC_MIRROR_UNKNOWN_STABLE_SECONDS", 2.0
)
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
        # Monotonic time anybody was last seen in frame; drives the
        # NO_PERSON_LABEL fallback. None until the first sighting.
        self.last_presence_time = None
        # Monotonic time an unrecognized face was first seen in the current
        # stretch of non-recognition; drives the UNRECOGNIZED_FACE_LABEL
        # announcement. None while nobody is there or somebody is recognized.
        self.unknown_since = None

        # MagicMirror settings as instance attributes
        self.magic_mirror_enabled = MAGIC_MIRROR_ENABLED
        self.magic_mirror_api_url = MAGIC_MIRROR_API_URL
        self.magic_mirror_api_token = MAGIC_MIRROR_API_TOKEN
        self.magic_mirror_min_gesture_confidence = MAGIC_MIRROR_MIN_GESTURE_CONFIDENCE
        self.magic_mirror_min_face_confidence = MAGIC_MIRROR_MIN_FACE_CONFIDENCE

        # Initialize MagicMirrorHandler if MagicMirror integration is enabled
        self.magic_mirror_handler = None
        if self.magic_mirror_enabled and self.magic_mirror_api_url:
            self.magic_mirror_handler = MagicMirrorHandler(
                self.magic_mirror_api_url, self.magic_mirror_api_token
            )

    def face_is_actionable(self, person_label, confidence):
        """
        Whether a face classification is confident enough for the mirror to act on.

        This must be consulted BEFORE the label reaches
        ``update_current_person``, not only before the POST. The label switch
        is what fires face_recognition, and it fires exactly once per change -
        so a sub-threshold classification that was allowed to advance the
        state machine would consume that one switch and leave the display
        stuck on the previous person forever: every later, confident sighting
        of the same person is no longer a change. Sharing one predicate
        between the state machine and the sender is what keeps them from
        disagreeing about who is being shown.

        "Unknown" and "None" are exempt. Neither is an identification: the
        first carries confidence 0.0 by construction and the second is
        synthesized from an empty frame with no detection to score at all, so
        there is no similarity score for the floor to judge - applying it
        would suppress those events outright.
        """
        if person_label in (UNRECOGNIZED_FACE_LABEL, NO_PERSON_LABEL):
            return True
        return confidence is None or confidence >= self.magic_mirror_min_face_confidence

    def send_magic_mirror_action(self, action, face=None, confidence=None):
        """Forward a recognized action/face to the MagicMirror module (if enabled)."""
        if not (self.magic_mirror_enabled and self.magic_mirror_handler):
            return
        if action == "face_recognition":
            allowed = self.face_is_actionable(face, confidence)
            min_confidence = self.magic_mirror_min_face_confidence
        else:
            min_confidence = self.magic_mirror_min_gesture_confidence
            allowed = confidence is None or confidence >= min_confidence
        if not allowed:
            hailo_logger.debug(
                f"Skipping MagicMirror action '{action}': confidence {confidence:.2f} "
                f"below minimum {min_confidence:.2f}."
            )
            return
        self.magic_mirror_handler.send_action(action=action, face=face, confidence=confidence)

    def update_current_person(self, track_id, person_label):
        """
        Debounced, per face track: accept a label for this track only after it
        has been seen FACE_STABLE_FRAMES consecutive times, so confidence
        flicker around the recognition threshold doesn't re-fire actions.
        Tracks are independent - with two people in frame, each face
        accumulates its own count and neither resets the other's (a single
        global challenger would starve every person after the first).

        Only real identities come through here. "Unknown" is handled by
        update_unknown instead: it needs to accumulate across face tracks,
        which a per-track counter cannot do.

        Returns True when the mirror should act on this label: either it
        stabilized and differs from the person currently being shown (a
        switch), or it is the same person returning after being unseen on
        every track for at least FACE_REFIRE_ABSENCE_SECONDS (a re-greeting).
        The absence window applies only to that second case, so tracker ID
        churn (occlusion re-issues track IDs for the same person within
        seconds) doesn't re-fire the action for someone who never left.
        A fired switch becomes ``current_person_label`` (used to tag
        gestures) and resets gesture state so a swipe can't span two people;
        a suppressed re-stabilization is a pure presence-refresh with no side
        effects.
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
        if person_label == self.current_person_label:
            # Already the person being shown. Only a real absence re-fires (a
            # genuine return deserves a fresh greeting); anything shorter is
            # tracker ID churn - they never left the scene, so don't retag
            # gestures or wipe in-progress gesture history.
            return last_seen is None or (now - last_seen) >= FACE_REFIRE_ABSENCE_SECONDS
        # A different label than the one being shown is always a switch, even
        # if that label was seen moments ago on another track. "Unknown" is
        # near-continuously present (every unrecognized face in the background
        # refreshes its last-seen time), so gating the switch on absence left
        # the mirror stuck on the last recognized person forever.
        self.current_person_label = person_label
        self.gesture_tracks.clear()
        self.latest_gesture_frame.clear()
        return True

    def update_presence(self, someone_present):
        """
        Track whether anybody is in frame, and report when the mirror should
        fall back to NO_PERSON_LABEL.

        Returns True once per departure: when the frame has been continuously
        empty for EMPTY_FRAME_SECONDS and the mirror is not already showing
        NO_PERSON_LABEL. Subsequent empty frames return False, so the action
        fires once rather than every frame for as long as the room stays empty.

        That includes startup. Before any label has been sent the mirror sits
        on whatever its configured home page is, and nothing guarantees that
        is the empty-room page (MMM-pages commonly homes on the "Unknown"
        page). So an empty room at launch is announced like any departure,
        once the dwell time confirms it - otherwise the mirror would claim an
        unrecognized person is present until somebody first walks by.

        Falling back also makes the next arrival a clean switch: whoever walks
        up next differs from the label being shown, so their action fires
        immediately instead of waiting out FACE_REFIRE_ABSENCE_SECONDS.
        """
        now = time.monotonic()
        if someone_present:
            self.last_presence_time = now
            return False
        if self.last_presence_time is None:
            # First frame since startup, and it is empty: start the dwell
            # clock from here.
            self.last_presence_time = now
            return False
        if self.current_person_label == NO_PERSON_LABEL:
            return False
        if now - self.last_presence_time < EMPTY_FRAME_SECONDS:
            return False
        self.current_person_label = NO_PERSON_LABEL
        # A swipe cannot span an empty frame.
        self.gesture_tracks.clear()
        self.latest_gesture_frame.clear()
        self.unknown_since = None
        return True

    def update_unknown(self, someone_present, recognized_seen, unknown_seen):
        """
        Report when an unrecognized face has been around long enough to
        announce a stranger.

        Timed in wall-clock seconds and accumulated ACROSS face tracks, not
        within one. The face tracker re-issues IDs about once a second even for
        somebody standing still, so a per-track counter resets long before any
        meaningful threshold and "Unknown" never fires at all.

        Once started, the clock is cleared only by somebody being recognized,
        or by update_presence once the room has been empty for
        EMPTY_FRAME_SECONDS. It is deliberately NOT cleared by a single frame
        without a face, nor by a frame that merely lacks an Unknown
        classification. The face detector drops out for a frame here and there
        constantly, and a fresh face track spends its first skip_frames
        unclassified; resetting on either meant the clock never survived long
        enough to fire. "Has the person left" is update_presence's
        (debounced) call, not this method's.

        A recognized face anywhere in frame wins: that identification is what
        the mirror should act on, and an unrecognized face in the background
        must not override it.
        """
        now = time.monotonic()
        if recognized_seen:
            if self.unknown_since is not None:
                hailo_logger.info("Unknown-face timer cleared: a face was recognized.")
            self.unknown_since = None
            return False
        if self.unknown_since is None:
            if not unknown_seen:
                # Nobody unrecognized has had a verdict yet. Nothing to time.
                return False
            self.unknown_since = now
            hailo_logger.info(
                f"Unknown-face timer started; announcing in "
                f"{FACE_UNKNOWN_STABLE_SECONDS:.0f}s unless someone is recognized."
            )
            return False
        if self.current_person_label == UNRECOGNIZED_FACE_LABEL:
            return False
        if now - self.unknown_since < FACE_UNKNOWN_STABLE_SECONDS:
            return False
        if not someone_present:
            # Due, but this particular frame lost the face. Announce on the
            # next frame that has one rather than into an empty frame; if the
            # person really left, update_presence will clear the timer first.
            return False
        self.current_person_label = UNRECOGNIZED_FACE_LABEL
        self.gesture_tracks.clear()
        self.latest_gesture_frame.clear()
        return True

    def update_gesture(
        self,
        track_id,
        wrist_name,
        x,
        y,
        body_center_x,
        shoulder_width,
        bbox_width,
        bbox_height,
    ):
        """
        Track wrist movement and return a recognized gesture name when a swipe completes.

        Direction is reported from the person's perspective (mirror-style): "swipe_right"
        means the user moved their hand toward their own right. The camera feed is not
        horizontally flipped, so the user's right is toward decreasing image-x.

        A swipe is a cross-body motion, not just lateral travel: the wrist must
        start on its own side of the body midline and finish past the midline on
        the opposite side. That also pins each direction to one arm - the left
        wrist produces "swipe_right", the right wrist "swipe_left".

        Motion is measured relative to the body midline, and the body itself has
        to stay put, so only someone standing in front of the mirror can swipe:
        a person walking through frame carries the wrist along with the torso
        without ever moving the arm across the body.
        """
        current_frame = self.get_count()
        state_key = (track_id, wrist_name)
        history = self.gesture_tracks.setdefault(state_key, deque(maxlen=GESTURE_HISTORY_LENGTH))
        history.append((current_frame, x, y, body_center_x, shoulder_width))

        if len(history) < GESTURE_HISTORY_LENGTH:
            return None

        first_frame = history[0][0]
        # Drop stale windows where the wrist lingered (slow drift, not a deliberate swipe).
        if current_frame - first_frame > GESTURE_HISTORY_LENGTH * 2:
            history.clear()
            return None

        # Wrist travel relative to the body midline: whole-body translation
        # cancels out, leaving only motion the arm actually produced.
        xs = [entry[1] - entry[3] for entry in history]
        ys = [entry[2] for entry in history]
        centers = [entry[3] for entry in history]
        shoulder_widths = sorted(entry[4] for entry in history)
        # Median shoulder width: a robust scale for this person at this
        # distance, unaffected by the odd frame with collapsed shoulders.
        body_scale = shoulder_widths[len(shoulder_widths) // 2]
        if body_scale <= 0:
            body_scale = bbox_width * GESTURE_SHOULDER_WIDTH_FALLBACK_RATIO

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

        # Too little lateral travel to even be a swipe attempt: the common case
        # for an idle hand, so bail before the (logged) gates below.
        if abs(horizontal_delta) < horizontal_threshold:
            return None

        # How far the torso itself travelled across the window. A person
        # standing in front of the mirror sways and rotates into the swipe; a
        # person walking in or out crosses several shoulder widths.
        body_drift = max(centers) - min(centers)

        # Cross-body gate. Positions are already midline-relative, so the
        # midline is simply 0 and torso sway during the swipe can't move the
        # goalposts mid-gesture.
        margin = bbox_width * GESTURE_MIDLINE_MARGIN_RATIO
        if horizontal_delta < 0:
            # Toward the person's right (decreasing image-x): only the left
            # wrist coming across the body counts. The left wrist sits at
            # increasing image-x, so it must start on its own side of the
            # midline and finish clearly past it.
            gesture_name = "swipe_right"
            crossed_body = wrist_name == "left_wrist" and start_x > 0 and end_x < -margin
        else:
            gesture_name = "swipe_left"
            crossed_body = wrist_name == "right_wrist" and start_x < 0 and end_x > margin

        rejection = None
        if vertical_delta > vertical_threshold:
            rejection = f"vertical travel {vertical_delta:.0f}px > {vertical_threshold:.0f}px"
        elif consistency < GESTURE_DIRECTION_CONSISTENCY:
            rejection = f"direction consistency {consistency:.2f} < {GESTURE_DIRECTION_CONSISTENCY}"
        elif body_drift > body_scale * GESTURE_MAX_BODY_DRIFT_RATIO:
            rejection = (
                f"body moved {body_drift:.0f}px "
                f"({body_drift / body_scale:.2f} shoulder widths) - walking, not standing"
            )
        elif not crossed_body:
            rejection = (
                f"no cross-body motion for {wrist_name} "
                f"(midline-relative {start_x:.0f}px -> {end_x:.0f}px, margin {margin:.0f}px)"
            )
        if rejection:
            hailo_logger.debug(
                f"Gesture candidate rejected for ID {track_id} ({wrist_name}, "
                f"{horizontal_delta:+.0f}px): {rejection}"
            )
            return None

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
    # Is anybody actually in frame? Resolved up front, in its own pass: the
    # gesture stage in the main loop gates on it, and the order of `detections`
    # is not specified - a person can be iterated before any face - so it
    # cannot be accumulated as the main loop goes.
    #
    # This callback runs per buffer whether or not anything was detected, so an
    # empty frame is observable here and nowhere else.
    someone_present = any(
        d.get_label() == "face"
        and d.get_confidence() >= MAGIC_MIRROR_MIN_FACE_DETECTION_CONFIDENCE
        for d in detections
    )
    # Face-recognition verdicts seen this buffer, both only consumed after the
    # loop (so accumulating them in it is safe):
    #   recognized_seen - at least one face resolved to a real identity
    #   unknown_seen    - at least one face resolved to "no gallery match"
    recognized_seen = False
    unknown_seen = False
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
                    classification_confidence = classification.get_confidence()
                    if person_label == UNRECOGNIZED_FACE_LABEL:
                        string_to_print += 'Unknown person detected'
                    else:
                        string_to_print += f'Person recognition: {person_label} (Confidence: {classification_confidence:.1f})'
                    # A classification we are not confident enough to act on is
                    # logged above but must not touch the recognition state:
                    # accepting it would consume the label switch that fires the
                    # action, and the later confident sighting of the same person
                    # would no longer read as a change. Still unsure who this is,
                    # so leave the currently shown person alone.
                    actionable = user_data.face_is_actionable(
                        person_label, classification_confidence
                    )
                    if person_label == UNRECOGNIZED_FACE_LABEL:
                        # Accumulated across tracks and announced by
                        # update_unknown after the loop, not debounced per
                        # track like a real identity.
                        unknown_seen = True
                    elif actionable:
                        recognized_seen = True
                        # Forward a one-shot face_recognition action to
                        # MagicMirror whenever the recognized person changes.
                        if user_data.update_current_person(track_id, person_label):
                            user_data.send_magic_mirror_action(
                                action="face_recognition",
                                face=person_label,
                                confidence=classification_confidence,
                            )
                    if track_id > user_data.latest_track_id:
                        user_data.latest_track_id = track_id
                        print(string_to_print)
        elif label == "person":
            # A swipe only counts while a face is in frame. The pose stage
            # emits phantom person tracks that outlive everybody in the room
            # (observed: one track alive 47s with nobody there, firing swipes
            # the whole time), and gesture geometry alone cannot tell their
            # garbage keypoints from a real arm. A face detection is the
            # corroboration the pose stage lacks, and costs nothing real: you
            # are looking at a mirror when you gesture at it.
            if not someone_present:
                continue

            # For the same reason, person detections are not a presence signal
            # either: treating any of them as "somebody is here" pinned the
            # mirror permanently occupied and the empty-frame fallback could
            # never fire. Presence comes from face detections above
            # MAGIC_MIRROR_MIN_FACE_DETECTION_CONFIDENCE, the signal that floor
            # already made trustworthy.
            #
            # The cost is that turning away from the camera long enough for
            # EMPTY_FRAME_SECONDS reads as leaving. That is a fair trade for a
            # mirror - you face it to use it - and it self-corrects: the
            # fallback makes the next recognition a clean label switch, so
            # turning back re-recognizes immediately. Raise emptyFrameSeconds
            # if it still goes idle too eagerly.
            track_id = 0
            track = detection.get_objects_typed(hailo.HAILO_UNIQUE_ID)
            if len(track) > 0:
                track_id = track[0].get_id()

            landmarks = detection.get_objects_typed(hailo.HAILO_LANDMARKS)
            if not landmarks or not fmt or not width or not height:
                continue

            bbox = detection.get_bbox()
            points = landmarks[0].get_points()
            # Body midline (shoulder midpoint) that a wrist must cross for a swipe.
            left_shoulder = points[COCO_KEYPOINTS["left_shoulder"]]
            right_shoulder = points[COCO_KEYPOINTS["right_shoulder"]]
            shoulder_mid = (left_shoulder.x() + right_shoulder.x()) / 2
            body_center_x = int((shoulder_mid * bbox.width() + bbox.xmin()) * width)
            # Distance scale for the standing-still gate: unlike the bbox, this
            # doesn't change when the arms move.
            shoulder_width = abs(left_shoulder.x() - right_shoulder.x()) * bbox.width() * width
            # Collect both wrists before acting on either: a frame where both
            # complete a swipe has to be thrown away wholesale, which can only
            # be decided once both are known.
            completed = []
            for wrist_name in ("left_wrist", "right_wrist"):
                point = points[COCO_KEYPOINTS[wrist_name]]
                x = int((point.x() * bbox.width() + bbox.xmin()) * width)
                y = int((point.y() * bbox.height() + bbox.ymin()) * height)
                gesture_name = user_data.update_gesture(
                    track_id=track_id,
                    wrist_name=wrist_name,
                    x=x,
                    y=y,
                    body_center_x=body_center_x,
                    shoulder_width=shoulder_width,
                    bbox_width=bbox.width() * width,
                    bbox_height=bbox.height() * height,
                )
                if gesture_name:
                    completed.append((wrist_name, gesture_name))

            # Each direction is pinned to one arm, so two swipes completing on
            # the same frame are necessarily opposite ones - both hands sweeping
            # across the body in opposite directions at the same instant. Nobody
            # does that; it means this track's keypoints are noise, so neither
            # swipe is trustworthy. The usual culprit is a phantom pose, which
            # produced exactly this signature on every spurious swipe observed.
            if len(completed) > 1:
                hailo_logger.debug(
                    f"Discarding {len(completed)} simultaneous gestures for ID "
                    f"{track_id} ({', '.join(f'{w}->{g}' for w, g in completed)}): "
                    f"both wrists cannot cross the body at once."
                )
                continue

            for wrist_name, gesture_name in completed:
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

    # An unrecognized face that has stuck around long enough: announce the
    # stranger. Checked before presence so that a frame which is both empty and
    # carries a stale Unknown resolves as empty - update_presence clears the
    # Unknown clock itself.
    if user_data.update_unknown(someone_present, recognized_seen, unknown_seen):
        print(
            f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]: '
            f'Unrecognized face present for {FACE_UNKNOWN_STABLE_SECONDS:.0f}s'
        )
        user_data.send_magic_mirror_action(
            action="face_recognition",
            face=UNRECOGNIZED_FACE_LABEL,
        )

    # Nobody in frame for long enough: hand the mirror back to its idle state.
    # No detection means no confidence to report, so none is sent.
    if user_data.update_presence(someone_present):
        print(
            f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]: '
            f'Frame empty for {EMPTY_FRAME_SECONDS:.0f}s - nobody present'
        )
        user_data.send_magic_mirror_action(
            action="face_recognition",
            face=NO_PERSON_LABEL,
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
