# region imports
# Standard library imports
import queue
import threading

from hailo_apps.python.core.common.hailo_logger import get_logger

# endregion imports

hailo_logger = get_logger(__name__)


class BackgroundWorker:
    """Single daemon thread draining a queue of callables.

    For work that must not run on a GStreamer buffer callback (blocking HTTP,
    file I/O): ``submit()`` never blocks the caller. With ``max_items`` set,
    the queue is bounded and ``submit()`` returns False instead of blocking
    when it is full; with ``max_items=0`` the queue is unbounded. Exceptions
    from submitted callables are logged, never raised, so one bad task cannot
    kill the worker thread.
    """

    def __init__(self, name, max_items=0):
        self._queue = queue.Queue(maxsize=max_items)
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    def submit(self, func, *args, **kwargs):
        """Queue ``func(*args, **kwargs)``; returns False if the queue is full."""
        try:
            self._queue.put_nowait((func, args, kwargs))
            return True
        except queue.Full:
            return False

    def stop(self):
        """Ask the worker thread to exit after draining queued tasks."""
        self._queue.put(None)

    def _loop(self):
        while True:
            item = self._queue.get()
            if item is None:  # stop() sentinel
                break
            func, args, kwargs = item
            try:
                func(*args, **kwargs)
            except Exception as e:
                hailo_logger.warning(f"Background task {func!r} failed: {e!s}")
            self._queue.task_done()
