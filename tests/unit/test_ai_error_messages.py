from app.services.error_handler import ErrorCode, classify_exception


def test_daily_quota_does_not_promise_seconds_or_blame_request():
    code, text = classify_exception(RuntimeError('429 RESOURCE_EXHAUSTED GenerateRequestsPerDay quota limit exceeded'))
    assert code == ErrorCode.RATE_LIMIT
    assert 'ngày' in text and 'vài giây' not in text


def test_provider_unavailable_and_invalid_json_are_technical_failures():
    code, _ = classify_exception(RuntimeError('503 UNAVAILABLE high demand'))
    assert code == ErrorCode.LLM_TIMEOUT
    code, text = classify_exception(ValueError('Expecting value: line 1 column 1'))
    assert code == ErrorCode.LLM_ERROR
    assert 'ngoài phạm vi' not in text
