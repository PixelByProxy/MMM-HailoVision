# region imports
# Standard library imports
from collections import OrderedDict
# endregion imports


class BoundedLruDict:
    """Small bounded LRU map for per-track state in an always-on pipeline.

    Track IDs increase monotonically for as long as the pipeline runs, so any
    dict keyed by them must be bounded or it grows forever. Eviction must be
    least-recently-USED, not insertion-order FIFO: the longest-present track
    is usually the person actually using the mirror, and a FIFO would evict
    exactly that entry first. Reading or writing a key marks it
    most-recently-used; inserting past ``max_entries`` evicts the LRU entry.
    """

    def __init__(self, max_entries):
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._data = OrderedDict()

    def get(self, key, default=None):
        if key not in self._data:
            return default
        self._data.move_to_end(key)
        return self._data[key]

    def setdefault(self, key, default):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        self[key] = default
        return default

    def pop(self, key, default=None):
        return self._data.pop(key, default)

    def clear(self):
        self._data.clear()

    def __setitem__(self, key, value):
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    def __getitem__(self, key):
        self._data.move_to_end(key)
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def __len__(self):
        return len(self._data)
