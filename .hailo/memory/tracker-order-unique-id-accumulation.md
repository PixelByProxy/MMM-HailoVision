---
name: tracker-order-unique-id-accumulation
description: Two serial hailotrackers cause unbounded HAILO_UNIQUE_ID accumulation -> FPS collapse; face branch must precede pose branch
metadata: 
  node_type: memory
  type: project
  originSessionId: 810dfd95-366a-4d75-b75a-95b2269d6c81
  modified: 2026-07-18T20:51:25.383Z
---

The magic_mirror pipeline collapsed from 30 FPS to <1 FPS after 30s–5min on
camera input (fixed 2026-07-18). Root cause: pose tracker ran before the face
tracker; the face tracker (class_id=-1 = all classes, keep_past_metadata=true)
matched pose "person" detections and re-attached the pose tracker's foreign
HAILO_UNIQUE_ID as "past metadata" every frame (+1/frame, observed 3,680 ids on
one detection). A hailotracker dedups its own id but not another tracker's.
Long-lived tracks (person standing at a mirror, static false positives) grow
until hailooverlay/cropper choke (seconds per frame, RSS +700MB).

**Why:** looks like device/model slowness but is metadata growth; file-input
tests can miss it because moving subjects churn tracks and reset the pile.

**How to apply:** keep the face branch (detection->tracker->cropper) BEFORE the
pose branch in get_pipeline_string (comment in magic_mirror_pipeline.py). Never
let a keep_past_metadata=true tracker see detections that already carry another
tracker's unique id. Diagnose recurrences by counting detection sub-objects in
the app callback (HAILO_MM_DEBUG_COUNTS-style census). Face detections are
class_id=-1, person (yolov8 pose) is class_id=0, so hailotracker class-id
filtering cannot select faces — ordering is the only isolation mechanism.
