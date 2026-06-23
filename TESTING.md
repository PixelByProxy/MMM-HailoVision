# Testing

Quick guide for running the hailo-apps test suite. For full details on test
categories, fixtures, and writing new tests, see `tests/README.md`.

## Setup

```bash
source setup_env.sh
pip install -e .
```

## Run All Tests

```bash
./run_tests.sh
```

## Run a Specific Suite

```bash
./run_tests.sh --sanity      # Environment + config integrity checks
./run_tests.sh --install     # Installation/resource validation
./run_tests.sh --pipelines   # Pipeline functional tests
./run_tests.sh --standalone  # Standalone app smoke tests
./run_tests.sh --genai       # GenAI app tests
```

## Run with pytest Directly

```bash
pytest tests/ -v                          # All tests
pytest tests/test_sanity_check.py -v      # Single file
pytest tests/ -m sanity                   # By marker
pytest tests/ -k "detection"              # By keyword
```

## Useful Flags

```bash
./run_tests.sh --apps detection,pose_estimation  # Limit to specific apps
./run_tests.sh --no-download                     # Skip resource download
```

## Notes

- Tests marked `requires_device` need a Hailo accelerator connected.
- Tests marked `requires_gstreamer` need GStreamer installed.
- See `tests/README.md` for fixtures, markers, and how to add new tests.
