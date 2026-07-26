from portfolio_news.retrying import is_transient_error, retry_call


class StatusError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


def test_retries_transient_status_with_bounded_exponential_delay():
    calls = 0
    sleeps = []

    def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise StatusError(429)
        return "ok"

    assert retry_call(flaky, operation="test", attempts=3, sleep=sleeps.append) == "ok"
    assert calls == 3
    assert sleeps == [1.0, 2.0]


def test_does_not_retry_permanent_status():
    calls = 0

    def permanent():
        nonlocal calls
        calls += 1
        raise StatusError(400)

    try:
        retry_call(permanent, operation="test", attempts=3, sleep=lambda _: None)
    except StatusError:
        pass
    assert calls == 1
    assert is_transient_error(StatusError(503))
    assert not is_transient_error(StatusError(403))

