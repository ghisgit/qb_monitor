import threading
import time

from _breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerOpenError


class TestCircuitBreaker:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3, recovery_timeout=60.0))
        assert cb.state == "CLOSED"
        assert cb.allow_request()

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3, recovery_timeout=60.0))
        cb.on_failure()
        assert cb.state == "CLOSED"
        cb.on_failure()
        assert cb.state == "CLOSED"
        cb.on_failure()
        assert cb.state == "OPEN"
        assert not cb.allow_request()

    def test_allows_request_in_half_open(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.05))
        cb.on_failure()
        assert cb.state == "OPEN"
        assert not cb.allow_request()
        time.sleep(0.06)
        assert cb.allow_request()
        assert cb.state == "HALF_OPEN"

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.05))
        cb.on_failure()
        time.sleep(0.06)
        cb.allow_request()
        cb.on_success()
        assert cb.state == "CLOSED"

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=60.0))
        cb.on_failure()
        assert cb.state == "OPEN"
        cb._state = "HALF_OPEN"
        cb.on_failure()
        assert cb.state == "OPEN"

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3, recovery_timeout=60.0))
        cb.on_failure()
        cb.on_failure()
        cb.on_success()
        cb.on_failure()
        assert cb.state == "CLOSED"

    def test_thread_safety(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=100, recovery_timeout=60.0))
        errors = []

        def hammer():
            for _ in range(50):
                try:
                    if cb.allow_request():
                        cb.on_success()
                    else:
                        cb.on_failure()
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_circuit_breaker_open_error(self):
        err = CircuitBreakerOpenError("test")
        assert isinstance(err, Exception)
        assert "test" in str(err)
