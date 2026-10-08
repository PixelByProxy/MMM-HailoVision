# region imports
# Standard library imports
import os
import shutil
import json
import sys
import tempfile
import time
import uuid
import setproctitle
from pathlib import Path
os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

# Third-party imports
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
import numpy as np
from PIL import Image

# Local application-specific imports
import hailo
from hailo import HailoTracker
from hailo_apps.python.core.common.background_worker import BackgroundWorker
from hailo_apps.python.core.common.bounded_lru import BoundedLruDict
from hailo_apps.python.core.common.db_handler import (
    UNRECOGNIZED_FACE_LABEL,
    DatabaseHandler,
    Record,
)
from hailo_apps.python.core.common.core import (
    get_pipeline_parser,
    get_resource_path,
    handle_list_models_flag,
    configure_multi_model_hef_path,
    resolve_hef_paths,
)
from hailo_apps.python.core.common.buffer_utils import get_numpy_from_buffer_efficient, get_caps_from_pad
from hailo_apps.python.core.common.env_utils import get_env_float
from hailo_apps.python.core.gstreamer.gstreamer_app import GStreamerApp
from hailo_apps.python.core.common.defines import (
    RESOURCES_SO_DIR_NAME, 
    MAGIC_MIRROR_PIPELINE,
    MAGIC_MIRROR_APP_TITLE,
    POSE_ESTIMATION_PIPELINE,
    POSE_ESTIMATION_POSTPROCESS_FUNCTION,
    POSE_ESTIMATION_POSTPROCESS_SO_FILENAME,
    FACE_DETECTION_POSTPROCESS_SO_FILENAME, 
    FACE_RECOGNITION_POSTPROCESS_SO_FILENAME, 
    FACE_ALIGN_POSTPROCESS_SO_FILENAME, 
    FACE_CROP_POSTPROCESS_SO_FILENAME,
    MAGIC_MIRROR_VIDEO_NAME,
    FACE_RECON_TRAIN_DIR_NAME,
    FACE_RECON_SAMPLES_DIR_NAME,
    RESOURCES_JSON_DIR_NAME,
    FACE_DETECTION_JSON_NAME,
    DEFAULT_LOCAL_RESOURCES_PATH,
    FACE_RECON_DATABASE_DIR_NAME,
    FACE_RECON_LOCAL_SAMPLES_DIR_NAME,
    BASIC_PIPELINES_VIDEO_EXAMPLE_NAME,
    SCRFD_10G_POSTPROCESS_FUNCTION,
    SCRFD_2_5G_POSTPROCESS_FUNCTION,
    IMAGE_EXTENSIONS,
    HAILO8_ARCH,
    HAILO10H_ARCH,
    HAILO8L_ARCH
)
from hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines import QUEUE, INFERENCE_PIPELINE, INFERENCE_PIPELINE_WRAPPER, TRACKER_PIPELINE, USER_CALLBACK_PIPELINE, DISPLAY_PIPELINE, CROPPER_PIPELINE
from hailo_apps.python.core.common.hailo_logger import get_logger

hailo_logger = get_logger(__name__)
# endregion imports

# Files in a person's training folder that are fed to the pipeline; anything
# else (stray .DS_Store, READMEs, ...) is skipped instead of aborting the run.
# Derived from the shared project-wide policy so it can't drift; webp is
# additionally accepted here because decodebin handles it fine for training.
TRAIN_IMAGE_EXTENSIONS = set(IMAGE_EXTENSIONS) | {".webp"}

# Longest edge a training image is fed to the pipeline at; see
# prepare_train_image. Face detection downsamples to its own small network input
# anyway, so this only bounds decode/scale cost and memory.
TRAIN_MAX_IMAGE_DIMENSION = 2048

class GStreamerMagicMirrorApp(GStreamerApp):
    # Cap on track_id_frame_count entries; stale track IDs are evicted (see
    # vector_db_callback) so a 24/7 run doesn't grow the dict forever.
    TRACK_STATE_MAX_ENTRIES = 1000

    def __init__(self, app_callback, user_data, parser=None):
        if parser is None:
            parser = get_pipeline_parser()
        parser.add_argument("--mode", default='run', help="The mode of the application: run, train, delete")
        parser.add_argument("--headless", action="store_true",
                            help="Run without a preview window (uses fakesink instead of autovideosink). "
                                 "Face/gesture detection still runs.")

        # Configure --hef-path for multi-model support (face detection + face recognition + pose)
        configure_multi_model_hef_path(parser)
        
        # Handle --list-models flag before full initialization
        handle_list_models_flag(parser, MAGIC_MIRROR_PIPELINE)
        
        super().__init__(parser, user_data)
        setproctitle.setproctitle(MAGIC_MIRROR_APP_TITLE)

        # Headless: drop the on-screen preview so the app can run without a
        # display (e.g. over SSH or as a service). Detection/POST logic is
        # unaffected; only the video sink changes.
        if self.options_menu.headless:
            self.video_sink = "fakesink"

        # Criteria for when a candidate frame is good enough to try recognize a person from it (e.g., skip the first few frames since in them person only entered the frame and usually is blurry)
        json_file_path = os.path.join(os.path.dirname(__file__), "face_recon_algo_params.json")
        with open(json_file_path, "r") as json_file:
            self.algo_params = json.load(json_file)
        # 1. How many frames to skip between detection attempts: avoid processing first frames since usually they are blurry since person just entered the frame, see self.track_id_frame_count
        self.skip_frames = self.algo_params['skip_frames']
        # 2. Confidence threshold for face classification: if the confidence is below this value, the face will not be recognized
        self.lance_db_vector_search_classification_confidence_threshold = self.algo_params['lance_db_vector_search_classification_confidence_threshold']
        # Both for face detection & recognition networks (not tunable from the UI)
        self.batch_size = self.algo_params['batch_size']
        # 3. Minimum face-DETECTION confidence before a crop is worth
        # recognizing at all. Distinct from the two thresholds above: those
        # judge "which person is this", this one judges "is this even a face".
        # Set by the MMM-HailoVision launcher from its `minFaceDetectionConfidence`
        # option; the JSON value is the fallback for running the app directly.
        self.min_face_detection_confidence = get_env_float(
            "HAILO_MAGIC_MIRROR_MIN_FACE_DETECTION_CONFIDENCE",
            self.algo_params.get('min_face_detection_confidence', 0.6),
        )

        # Initialize directories
        current_dir = Path(__file__).parent
        # The training-images directory can be relocated with
        # HAILO_MAGIC_MIRROR_TRAIN_DIR (the MMM-HailoVision launcher sets it
        # from its `trainingDir` config option). Default lives next to this
        # script.
        train_dir_env = os.environ.get("HAILO_MAGIC_MIRROR_TRAIN_DIR", "").strip()
        # Only the default (bundled) directory gets seeded with the sample
        # training images when empty; a user-provided directory is left as-is.
        self.using_default_train_dir = not train_dir_env
        if train_dir_env:
            self.train_images_dir = Path(train_dir_env).expanduser()
        else:
            self.train_images_dir = current_dir / FACE_RECON_TRAIN_DIR_NAME
        self.samples_dir = current_dir / FACE_RECON_SAMPLES_DIR_NAME
        self.database_dir = current_dir / FACE_RECON_DATABASE_DIR_NAME
        os.makedirs(self.train_images_dir, exist_ok=True)
        os.makedirs(self.samples_dir, exist_ok=True)

        # Initialize the database and table
        self.db_handler = DatabaseHandler(db_name='persons.db', 
                                          table_name='persons', 
                                          schema=Record, 
                                          threshold=self.lance_db_vector_search_classification_confidence_threshold,
                                          database_dir=self.database_dir,
                                          samples_dir=self.samples_dir)

        # Architecture is already handled by GStreamerApp parent class
        # Use self.arch which is set by parent
        
        if BASIC_PIPELINES_VIDEO_EXAMPLE_NAME in self.video_source:
            self.video_source = get_resource_path(pipeline_name=None, resource_type=DEFAULT_LOCAL_RESOURCES_PATH, arch=self.arch, model=MAGIC_MIRROR_VIDEO_NAME)
        
        self.current_file = None  # for train mode - the file the pipeline reads
        self.current_person = None  # for train mode - label for self.current_file
        self.current_source_key = None  # for train mode - DB identity of the source image
        self.processed_names = {}  # name -> global_id for train mode - pipeline will be playing for 2 seconds, so we need to ensure each person will be processed only once
        self.processed_files = set()  # for train mode - pipeline will be playing for 2 seconds, so we need to ensure each file will be processed only once

        # Resolve HEF paths for multi-model app. All three models are registered
        # under MAGIC_MIRROR_PIPELINE in resources_config.yaml (detection,
        # recognition, pose). Train mode only needs the first two (face
        # detection + face recognition), so drop the pose model after resolving.
        hef_paths = self.options_menu.hef_path
        if self.options_menu.mode != 'run' and hef_paths:
            hef_paths = hef_paths[:2]
        models = resolve_hef_paths(hef_paths=hef_paths, app_name=MAGIC_MIRROR_PIPELINE, arch=self.arch)
        if self.options_menu.mode != 'run':
            models = models[:2]
        self.hef_path_detection = models[0].path
        self.hef_path_recognition = models[1].path
        self.hef_path_pose = models[2].path if len(models) > 2 else None
    
        if self.arch in (HAILO8_ARCH, HAILO10H_ARCH):
            self.detection_func = SCRFD_10G_POSTPROCESS_FUNCTION
        elif self.arch == HAILO8L_ARCH:
            self.detection_func = SCRFD_2_5G_POSTPROCESS_FUNCTION
        else:
            hailo_logger.error("Unsupported Hailo architecture: %s", self.arch)
            print(
                f"ERROR: Unsupported Hailo architecture: {self.arch}. "
                "Supported architectures are: hailo8, hailo8l, hailo10h.",
                file=sys.stderr
            )
            sys.exit(1)
        
        self.recognition_func = "filter"
        self.cropper_func = "face_recognition"

        # Set the post-processing shared object file
        self.post_process_so_scrfd = get_resource_path(pipeline_name=None, resource_type=RESOURCES_SO_DIR_NAME, arch=self.arch, model=FACE_DETECTION_POSTPROCESS_SO_FILENAME)
        self.post_process_so_face_recognition = get_resource_path(pipeline_name=None, resource_type=RESOURCES_SO_DIR_NAME, arch=self.arch, model=FACE_RECOGNITION_POSTPROCESS_SO_FILENAME)
        self.post_process_so_face_align = get_resource_path(pipeline_name=None, resource_type=RESOURCES_SO_DIR_NAME, arch=self.arch, model=FACE_ALIGN_POSTPROCESS_SO_FILENAME)
        self.post_process_so_cropper = get_resource_path(pipeline_name=None, resource_type=RESOURCES_SO_DIR_NAME, arch=self.arch, model=FACE_CROP_POSTPROCESS_SO_FILENAME)
        self.post_process_so_pose = get_resource_path(POSE_ESTIMATION_PIPELINE, RESOURCES_SO_DIR_NAME, self.arch, POSE_ESTIMATION_POSTPROCESS_SO_FILENAME)
        
        # Callbacks: bindings between the C++ & Python code
        self.app_callback = app_callback
        self.vector_db_callback_name = "vector_db_callback"
        self.train_vector_db_callback_name = "train_vector_db_callback"
        self.precrop_guard_name = "precrop_guard"
        if self.options_menu.mode == 'run':
            self.create_pipeline()  # initialize self.pipeline
            self.connect_vector_db_callback()
            self.connect_precrop_guard_callback()
        # Train mode builds (and tears down) a fresh pipeline per image in
        # run_training(); don't create one here. At this point self.current_file
        # is still None, so a pipeline built now would be a junk
        # 'multifilesrc location=None' that holds a vdevice and is never used.
        # Per-track frame counters (LRU-bounded) - avoid processing first frames since usually they are blurry since person just entered the frame
        self.track_id_frame_count = BoundedLruDict(self.TRACK_STATE_MAX_ENTRIES)
        self.tracker = HailoTracker.get_instance()  # tracker object

        # Saves training sample images off the buffer-callback thread.
        self.image_saver = BackgroundWorker(name="image-saver")

    def get_pipeline_string(self):
        source_pipeline = self.get_source_pipeline()
        pose_pipeline_wrapper = ""
        pose_tracker_pipeline = ""
        if self.options_menu.mode == 'run':
            pose_pipeline = INFERENCE_PIPELINE(hef_path=self.hef_path_pose, post_process_so=self.post_process_so_pose, post_function_name=POSE_ESTIMATION_POSTPROCESS_FUNCTION, batch_size=self.batch_size, name='pose_inference')
            pose_pipeline_wrapper = INFERENCE_PIPELINE_WRAPPER(pose_pipeline, name='pose_inference_wrapper')
            pose_tracker_pipeline = TRACKER_PIPELINE(class_id=0, name='hailo_pose_tracker')
        detection_pipeline = INFERENCE_PIPELINE(hef_path=self.hef_path_detection, post_process_so=self.post_process_so_scrfd, post_function_name=self.detection_func, batch_size=self.batch_size, config_json=get_resource_path(pipeline_name=None, resource_type=RESOURCES_JSON_DIR_NAME, arch=self.arch, model=FACE_DETECTION_JSON_NAME), name='face_detection_inference')
        detection_pipeline_wrapper = INFERENCE_PIPELINE_WRAPPER(detection_pipeline, name='face_detection_wrapper')
        tracker_pipeline = TRACKER_PIPELINE(class_id=-1, kalman_dist_thr=0.7, iou_thr=0.8, init_iou_thr=0.9, keep_new_frames=2, keep_tracked_frames=6, keep_lost_frames=8, keep_past_metadata=True, name='hailo_face_tracker')
        mobile_facenet_pipeline = INFERENCE_PIPELINE(hef_path=self.hef_path_recognition, post_process_so=self.post_process_so_face_recognition, post_function_name=self.recognition_func, batch_size=self.batch_size, config_json=None, name='face_recognition_inference')
        # Identity element whose probe (precrop_guard_callback) removes face
        # detections with degenerate bboxes before they reach the cropper: the
        # tracker emits Kalman-PREDICTED boxes for briefly unmatched tracks, so
        # a face leaving the frame can produce a box fully outside it - the
        # cropper then builds an empty crop and cv::resize aborts the process
        # (!ssize.empty()).
        precrop_guard_pipeline = USER_CALLBACK_PIPELINE(name=self.precrop_guard_name)
        cropper_pipeline = CROPPER_PIPELINE(inner_pipeline=(f'hailofilter so-path={self.post_process_so_face_align} '
                                                            f'name=face_align_hailofilter use-gst-buffer=true qos=false ! '
                                                            f'{QUEUE(name="detector_pos_face_align_q")} ! '
                                                            f'{mobile_facenet_pipeline}'),
                                            so_path=self.post_process_so_cropper, function_name=self.cropper_func, internal_offset=True)
        vector_db_callback_pipeline = USER_CALLBACK_PIPELINE(name=self.vector_db_callback_name)  # 'identity name' - is a GStreamer element that does nothing, but allows to add a probe to it
        user_callback_pipeline = USER_CALLBACK_PIPELINE()
        display_pipeline = DISPLAY_PIPELINE(video_sink=self.video_sink, sync=self.sync, show_fps=self.show_fps)

        if self.options_menu.mode == 'train':
            # Quote the location: training paths are user-provided (HAILO_MAGIC_MIRROR_TRAIN_DIR)
            # and an unquoted space would abort gst_parse_launch.
            source_pipeline = (f"multifilesrc location=\"{self.current_file}\" loop=true num-buffers=30 ! "  # each image 30 times
                               f"decodebin ! videoconvert n-threads=4 qos=false ! video/x-raw, format=RGB, pixel-aspect-ratio=1/1 ")
            vector_db_callback_pipeline = USER_CALLBACK_PIPELINE(name=self.train_vector_db_callback_name)
            display_pipeline = DISPLAY_PIPELINE(video_sink=self.video_sink, sync=self.sync, show_fps=self.show_fps)

        # ORDERING CONSTRAINT: the face branch (detection -> tracker -> cropper)
        # must run BEFORE the pose branch. The face tracker uses class_id=-1
        # (track everything) with keep_past_metadata=true; if pose runs first,
        # the face tracker also matches the pose "person" detections and
        # re-attaches the pose tracker's HAILO_UNIQUE_ID as "past metadata"
        # every frame. A long-lived person track (someone standing at the
        # mirror, or a static false positive) then accumulates thousands of
        # unique-id objects, and per-frame processing cost grows until the
        # pipeline collapses to <1 FPS. With face-first ordering, person
        # detections don't exist yet when the face tracker runs, and the pose
        # tracker (class_id=0) ignores face detections (class_id=-1).
        pipeline_parts = [
            source_pipeline,
            detection_pipeline_wrapper,
            tracker_pipeline,
            precrop_guard_pipeline,
            cropper_pipeline,
            vector_db_callback_pipeline,
        ]
        if pose_pipeline_wrapper:
            pipeline_parts.extend([pose_pipeline_wrapper, pose_tracker_pipeline])
        pipeline_parts.extend([
            user_callback_pipeline,
            display_pipeline,
        ])
        return ' ! '.join(pipeline_parts)

    def run(self):
        if self.options_menu.mode == 'run':
            super().run()  # start the Gstreamer pipeline
        else:  # train
            self.run_training()

    def run_training(self):
        """
        Iterate over the training folder structured with subfolders (person names),
        generates embeddings for each image, and stores them in the database with the person's name.
        If the default (bundled) training folder is empty, seed it with the example
        training images from the local resources folder. A user-configured folder
        (HAILO_MAGIC_MIRROR_TRAIN_DIR) is never seeded; if empty, training is skipped.
        """
        # Check if the directory is empty
        if not os.listdir(self.train_images_dir):
            if not self.using_default_train_dir:
                print(f"Training directory {self.train_images_dir} is empty; nothing to train.")
                return
            print(f"Training directory {self.train_images_dir} is empty. Copying default training images from local resources.")
            source_dir = get_resource_path(pipeline_name=None, resource_type=DEFAULT_LOCAL_RESOURCES_PATH, arch=self.arch, model=FACE_RECON_LOCAL_SAMPLES_DIR_NAME)
            for item in os.listdir(source_dir):
                source_path = os.path.join(source_dir, item)
                destination_path = os.path.join(self.train_images_dir, item)
                if os.path.isdir(source_path):
                    shutil.copytree(source_path, destination_path)
                else:
                    shutil.copy2(source_path, destination_path)

        print(f"Training on images from {self.train_images_dir}")
        scratch_dir = tempfile.mkdtemp(prefix="magic_mirror_train_")
        try:
            self.train_persons(scratch_dir)
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)
        print("Training completed")

    def train_persons(self, scratch_dir):
        """Embed every not-yet-trained image, one person folder at a time.

        scratch_dir holds any downscaled stand-ins built by prepare_train_image.
        """
        for person_name in sorted(os.listdir(self.train_images_dir)):
            person_folder = os.path.join(self.train_images_dir, person_name)
            if not os.path.isdir(person_folder):
                continue

            # Resume an existing person rather than skipping the whole folder:
            # reusing their global_id means newly added images append samples to
            # the record they already have instead of creating a second row under
            # the same label.
            existing_record = self.db_handler.get_record_by_label(label=person_name)
            trained_sources = set()
            if existing_record:
                self.processed_names[person_name] = existing_record["global_id"]
                trained_sources = self.trained_source_keys(existing_record)

            pending_images = []
            for image_file in sorted(os.listdir(person_folder)):
                if os.path.splitext(image_file)[1].lower() not in TRAIN_IMAGE_EXTENSIONS:
                    hailo_logger.info(f"Skipping non-image file: {image_file}")
                    continue
                image_path = os.path.join(person_folder, image_file)
                if self.train_source_key(image_path) in trained_sources:
                    hailo_logger.info(f"Skipping already-trained image: {image_file}")
                    continue
                pending_images.append(image_path)

            if not pending_images:
                continue
            print(f"Processing person: {person_name}")
            for image_path in pending_images:
                print(f"Processing image: {os.path.basename(image_path)}")
                # The pipeline may be handed a downscaled stand-in, so the label
                # and the DB identity come from the original image, not from
                # whatever path is actually fed in.
                self.current_person = person_name
                self.current_source_key = self.train_source_key(image_path)
                self.current_file = self.prepare_train_image(image_path, scratch_dir)
                self.create_pipeline()
                self.connect_train_vector_db_callback()
                self.connect_precrop_guard_callback()
                try:
                    self.pipeline.set_state(Gst.State.PLAYING)
                    time.sleep(2)
                except Exception as e:
                    print(f"Error processing image {os.path.basename(image_path)}: {e}")
                finally:
                    if self.pipeline:
                        # set_state(NULL) is asynchronous. Block until the
                        # transition completes so the hailonet vdevice is fully
                        # released before the next image builds a new pipeline -
                        # otherwise the next hailonet fails with
                        # HAILO_OUT_OF_PHYSICAL_DEVICES. Drop the reference so GC
                        # can reclaim the elements.
                        self.pipeline.set_state(Gst.State.NULL)
                        self.pipeline.get_state(5 * Gst.SECOND)
                        self.pipeline = None

    def prepare_train_image(self, image_path, scratch_dir):
        """Path the pipeline should actually read for a training image.

        Full-resolution phone stills (12MP+) have been seen to segfault the
        decode/detect path part-way through a run, which aborts training for
        every person after them. Anything longer than TRAIN_MAX_IMAGE_DIMENSION
        is therefore fed as a downscaled copy under scratch_dir. Detection runs
        at the network's own small input size, so the cap costs no accuracy.

        Falls back to the original path if the image can't be rescaled - that is
        no worse than the previous behaviour of always feeding the original.
        """
        try:
            with Image.open(image_path) as image:
                if max(image.size) <= TRAIN_MAX_IMAGE_DIMENSION:
                    return image_path
                original_size = image.size
                scaled = image.convert("RGB")
                scaled.thumbnail((TRAIN_MAX_IMAGE_DIMENSION, TRAIN_MAX_IMAGE_DIMENSION))
                scaled_path = os.path.join(scratch_dir, f"{uuid.uuid4().hex}.jpg")
                scaled.save(scaled_path, quality=95)
        except Exception as e:
            print(f"Could not downscale {os.path.basename(image_path)}, using original: {e}")
            return image_path
        print(f"Downscaled {os.path.basename(image_path)} from "
              f"{original_size[0]}x{original_size[1]} to {scaled.size[0]}x{scaled.size[1]}")
        return scaled_path

    def train_source_key(self, image_path):
        """Stable identity for a training image, as stored on its DB samples.

        Relative to the training directory ("Ryan/Ryan2.jpeg") so the key keeps
        matching if that directory is moved or HAILO_MAGIC_MIRROR_TRAIN_DIR is
        repointed; absolute only for paths outside it. The key is the path alone,
        so replacing an image in place with different content does NOT retrain it
        - delete the person's record to rebuild from scratch.
        """
        resolved = Path(image_path).resolve()
        try:
            return str(resolved.relative_to(Path(self.train_images_dir).resolve()))
        except ValueError:
            return str(resolved)

    def trained_source_keys(self, record):
        """Training images already embedded into a record.

        Samples written before source tracking existed carry no "source_path", so
        they match nothing and their images get embedded once more - harmless, but
        it double-weights those faces in the average embedding until the record is
        rebuilt.
        """
        samples = record.get("samples_json")
        if isinstance(samples, str):  # get_record_by_label leaves the JSON unparsed
            samples = json.loads(samples or "[]")
        return {s["source_path"] for s in (samples or []) if s.get("source_path")}

    def connect_vector_db_callback(self):
        identity = self.pipeline.get_by_name(self.vector_db_callback_name)
        if identity:
            identity_pad = identity.get_static_pad("src")  # src is the output of an element
            identity_pad.add_probe(Gst.PadProbeType.BUFFER, self.vector_db_callback, self.user_data)  # trigger - when the pad gets buffer
    
    def connect_train_vector_db_callback(self):
        identity = self.pipeline.get_by_name(self.train_vector_db_callback_name)
        if identity:
            identity_pad = identity.get_static_pad("src")  # src is the output of an element
            identity_pad.add_probe(Gst.PadProbeType.BUFFER, self.train_vector_db_callback, self.user_data)  # trigger - when the pad gets buffer

    def connect_precrop_guard_callback(self):
        identity = self.pipeline.get_by_name(self.precrop_guard_name)
        if identity:
            identity_pad = identity.get_static_pad("src")
            identity_pad.add_probe(Gst.PadProbeType.BUFFER, self.precrop_guard_callback, self.user_data)

    # Faces whose visible (frame-clamped) area is thinner than this fraction of
    # the frame produce empty/near-empty crops; they are useless for
    # recognition and crash the cropper's cv::resize when fully outside.
    PRECROP_MIN_VISIBLE_SIZE = 0.004

    def precrop_guard_callback(self, pad, info, user_data):
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        roi = hailo.get_roi_from_buffer(buffer)
        for detection in roi.get_objects_typed(hailo.HAILO_DETECTION):
            if detection.get_label() != 'face':
                continue
            bbox = detection.get_bbox()
            visible_w = min(1.0, bbox.xmax()) - max(0.0, bbox.xmin())
            visible_h = min(1.0, bbox.ymax()) - max(0.0, bbox.ymin())
            if visible_w < self.PRECROP_MIN_VISIBLE_SIZE or visible_h < self.PRECROP_MIN_VISIBLE_SIZE:
                roi.remove_object(detection)
        return Gst.PadProbeReturn.OK

    def save_image_file(self, frame, image_path):
        image = Image.fromarray(frame)
        image.save(image_path, format="JPEG", quality=85)  # Save as a compressed JPEG with quality 85
    
    def crop_frame(self, frame, bbox, width, height):
        # Retrieve the bounding box of the detection to save only the cropped area - useful in case there are more than 1 person in the frame
        # Add extra padding 0.15 to each side of the bounding box
        # Clamp the relative coordinates to the range [0, 1]
        x_min = max(0, min(bbox.xmin()-0.15, 1))
        y_min = max(0, min(bbox.ymin()-0.15, 1))
        x_max = max(0, min(bbox.xmax()+0.15, 1))
        y_max = max(0, min(bbox.ymax()+0.15, 1))

        # Scale the relative coordinates to absolute pixel values
        x_min = int(x_min * width)
        y_min = int(y_min * height)
        x_max = int(x_max * width)
        y_max = int(y_max * height)

        # Crop the frame to the detection area
        return frame[y_min:y_max, x_min:x_max]

    def vector_db_callback(self, pad, info, user_data):
        tracker_names = self.tracker.get_trackers_list()
        if not tracker_names:
            return Gst.PadProbeReturn.OK
        tracker_name = 'hailo_face_tracker' if 'hailo_face_tracker' in tracker_names else tracker_names[0]
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        roi = hailo.get_roi_from_buffer(buffer)
        
        # for each face detection
        for detection in (d for d in roi.get_objects_typed(hailo.HAILO_DETECTION) if d.get_label() == 'face'):
            # Don't try to recognize something the detector isn't confident is
            # a face. Partial faces, reflections and background objects come
            # through as low-confidence detections; their crops are junk, so
            # the gallery search either returns "Unknown" (which then drives a
            # spurious unknown-person event) or, worse, clears the similarity
            # threshold against a real person and mislabels them. Gating here
            # rather than in the app callback also keeps the per-frame vector
            # search - the most expensive step in this callback - off crops
            # that can never produce a usable embedding.
            if detection.get_confidence() < self.min_face_detection_confidence:
                continue

            track_id = detection.get_objects_typed(hailo.HAILO_UNIQUE_ID)[0].get_id() if detection.get_objects_typed(hailo.HAILO_UNIQUE_ID) else None

            # still in the skip frames period -skip
            if self.track_id_frame_count.get(track_id, 0) < self.skip_frames:
                self.track_id_frame_count[track_id] = self.track_id_frame_count.get(track_id, 0) + 1
                continue
            
            # after self.skip_frames
            embedding = detection.get_objects_typed(hailo.HAILO_MATRIX)  # face recognition embedding
            if len(embedding) == 0:
                continue  # if cropper pipeline element decided to pass the detection - it will arrive to this stage of the pipeline without face embedding
            if len(embedding) > 1:
                # Anomalous: exactly one embedding is expected. Remove them
                # all so the detection can't get stuck permanently >1 (the
                # tracker keeps past metadata, so leftovers would otherwise
                # persist and this detection would never be classified) and
                # classify on a fresh embedding next frame.
                hailo_logger.warning(
                    f"Multiple embeddings found for track ID {track_id}; discarding them and skipping this frame."
                )
                for matrix in embedding:
                    detection.remove_object(matrix)
                continue
            embedding_vector = np.array(embedding[0].get_data())
            person = self.db_handler.search_record(embedding=embedding_vector)  # most time consuming operation - search the database for the person with the closest embedding
            recognized = person['label'] != UNRECOGNIZED_FACE_LABEL
            # Clamp: cosine distance can exceed 1, which would yield a negative confidence.
            new_confidence = max(0.0, min(1.0, 1 - person['_distance']))
            classification = detection.get_objects_typed(hailo.HAILO_CLASSIFICATION)
            existing = classification[0] if classification else None
            # Keep the best classification this track has seen - but a no-match
            # never overwrites an identity already established on it. A track is
            # one face for its whole life, so "Unknown" from a blurred or
            # turned-away frame means "this frame was unusable", not "somebody
            # else"; letting it land would un-recognize a person still standing
            # in front of the mirror. An unrecognized track does take the first
            # real match that comes along (confidence 0.0 loses every compare).
            if existing is None or (recognized and existing.get_confidence() < new_confidence):
                if existing is not None:
                    detection.remove_object(existing)
                new_classification = hailo.HailoClassification(type='face_recon', label=person['label'], confidence=new_confidence)
                detection.add_object(new_classification)
                self.tracker.remove_classifications_from_track(tracker_name, track_id, 'face_recon')
                self.tracker.add_object_to_track(tracker_name, track_id, new_classification)
            
            # anyway re-process for "double-check" after self.skip_frames X 3
            self.track_id_frame_count[track_id] = -3 * self.skip_frames

        return Gst.PadProbeReturn.OK
    
    def train_vector_db_callback(self, pad, info, user_data):
        if self.current_source_key in self.processed_files:
            return Gst.PadProbeReturn.OK
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        fmt, width, height = get_caps_from_pad(pad)
        frame = get_numpy_from_buffer_efficient(buffer, fmt, width, height)
        roi = hailo.get_roi_from_buffer(buffer)
        if len(roi.get_objects_typed(hailo.HAILO_DETECTION)) == 0:
            print("No face detections found in the current frame.")
        for detection in roi.get_objects_typed(hailo.HAILO_DETECTION):
            print(detection.get_label())
        for detection in (d for d in roi.get_objects_typed(hailo.HAILO_DETECTION) if d.get_label() == "face"):
            embedding = detection.get_objects_typed(hailo.HAILO_MATRIX)
            if len(embedding) != 1:  # we will continue if new embedding exists - might be new person, or another image of existing person
                continue  # if cropper pipeline element decided to pass the detection - it will arrive to this stage of the pipeline without face embedding.
            # Read the embedding data BEFORE removing the object from the
            # detection - reading after the removal only works while the
            # binding happens to keep the object alive.
            embedding_vector = np.array(embedding[0].get_data())
            detection.remove_object(embedding[0])  # in case the detection pointer tracker pipeline element (from earlier side of the pipeline) holds is the same as the one we have, remove the embedding, so embedding similarity won't be part of the decision criteria
            cropped_frame = self.crop_frame(frame, detection.get_bbox(), width, height)
            image_path = os.path.join(self.samples_dir, f"{uuid.uuid4()}.jpeg")
            self.image_saver.submit(self.save_image_file, cropped_frame, image_path)
            # Both come from the original training image: self.current_file may be
            # a downscaled stand-in in a scratch dir, whose parent directory is not
            # the person's name. source_key records which training image this
            # sample came from, so a later run can tell this face is already
            # learned and only embed images it hasn't seen.
            name = self.current_person
            source_key = self.current_source_key
            if name in self.processed_names:
                self.db_handler.insert_new_sample(record=self.db_handler.get_record_by_id(self.processed_names[name]), embedding=embedding_vector, sample=image_path, timestamp=int(time.time()), source_path=source_key)
                print(f"Adding face to: {name}")
            else:
                person = self.db_handler.create_record(embedding=embedding_vector, sample=image_path, timestamp=int(time.time()), label=name, source_path=source_key)
                print(f"New person added with ID: {person['global_id']}")
                self.processed_names[name] = person['global_id']
            self.processed_files.add(self.current_source_key)
            return Gst.PadProbeReturn.OK  # in case of training - iterate exactly once per image
        return Gst.PadProbeReturn.OK
