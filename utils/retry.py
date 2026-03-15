"""Exponential backoff decorator for HTTP calls."""
from __future__ import annotations

import functools
import time
from typing import Callable, Type

import requests

from utils.logging import get_logger

log = get_logger(__name__)


def with_retry(
    max_retries: int = 3,
    backoff_base: float = 1.0,
    backoff_max: float = 30.0,
    retry_on: tuple[Type[Exception], ...] = (requests.RequestException, TimeoutError),
):
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = backoff_base
            for attempt in range(1, max_retries + 2):
                try:
                    return fn(*args, **kwargs)
                except retry_on as exc:
                    if attempt > max_retries:
                        log.error(f"{fn.__name__} failed after {max_retries} retries: {exc}")
                        raise
                    # Rate limit: wait longer
                    if hasattr(exc, "response") and exc.response is not None:
                        if exc.response.status_code == 429:
                            delay = 60.0
                    log.warning(f"{fn.__name__} attempt {attempt} failed ({exc}), retrying in {delay:.1f}s")
                    time.sleep(delay)
                    delay = min(delay * 2, backoff_max)
        return wrapper
    return decorator
