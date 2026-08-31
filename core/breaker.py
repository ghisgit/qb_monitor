import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class CircuitBreakerOpenError(Exception):
    """Request rejected because the circuit breaker is OPEN."""


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0


@dataclass
class RetryConfig:
    max_attempts: int = 3
    delay: float = 1.0
    backoff: float = 1.0
    cap: float = 30.0


class CircuitBreaker:
    def __init__(self, config: CircuitBreakerConfig | None = None):
        self._config = config or CircuitBreakerConfig()
        self._state = "CLOSED"
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def allow_request(self) -> bool:
        with self._lock:
            if self._state == "CLOSED":
                return True
            if self._state == "OPEN":
                if time.monotonic() - self._last_failure_time >= self._config.recovery_timeout:
                    logger.info("Circuit breaker: OPEN → HALF_OPEN (recovery timeout elapsed)")
                    self._state = "HALF_OPEN"
                    return True
                return False
            return True

    def on_success(self):
        with self._lock:
            self._failure_count = 0
            if self._state == "HALF_OPEN":
                logger.info("Circuit breaker: HALF_OPEN → CLOSED (success)")
                self._state = "CLOSED"

    def on_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self._config.failure_threshold:
                logger.info(
                    "Circuit breaker: CLOSED → OPEN (failure_count=%d >= threshold=%d)",
                    self._failure_count,
                    self._config.failure_threshold,
                )
                self._state = "OPEN"
