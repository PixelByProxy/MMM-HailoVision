"""Helpers for reading typed configuration values from environment variables."""
import os

from hailo_apps.python.core.common.hailo_logger import get_logger

hailo_logger = get_logger(__name__)


def get_env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def get_env_str(name, default=""):
    return os.getenv(name, default).strip()


def get_env_float(name, default=0.0):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except ValueError:
        hailo_logger.warning(f"Invalid float for {name}: {value!r}; using default {default}.")
        return default
