# Memory Redirect

Memory files are centralized in `.hailo/memory/`.
See `.hailo/memory/MEMORY.md` for the unified index.

Files:
- `.hailo/memory/common_pitfalls.md` — Bugs & anti-patterns (read on every task)
- `.hailo/memory/gen_ai_patterns.md` — VLM/LLM architecture patterns
- `.hailo/memory/pipeline_optimization.md` — GStreamer bottleneck fixes
- `.hailo/memory/camera_and_display.md` — Camera & OpenCV patterns
- `.hailo/memory/hailo_platform_api.md` — SDK usage patterns
- `.hailo/memory/tracker-order-unique-id-accumulation.md` — Tracker order: face branch before pose: second tracker re-attaches foreign HAILO_UNIQUE_IDs as past metadata, unbounded growth collapses FPS; fixed 2026-07-18
