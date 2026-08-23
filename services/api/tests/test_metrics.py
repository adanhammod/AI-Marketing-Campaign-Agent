from campaign_api.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL, UNMATCHED_PATH_LABEL


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def _histogram_count(histogram, **labels) -> float:
    samples = {sample.name: sample.value for sample in histogram.collect()[0].samples if sample.labels == labels}
    return samples[f"{histogram._name}_count"]


def test_metrics_endpoint_exposes_prometheus_text_format(client):
    response = client.get("/health/live")
    assert response.status_code == 200

    metrics_response = client.get("/metrics")

    assert metrics_response.status_code == 200
    assert "http_requests_total" in metrics_response.text
    assert "http_request_duration_seconds" in metrics_response.text


def test_http_requests_total_uses_route_template_not_raw_path(client):
    before = _counter_value(HTTP_REQUESTS_TOTAL, method="GET", path="/health/live", status="200")

    response = client.get("/health/live")

    after = _counter_value(HTTP_REQUESTS_TOTAL, method="GET", path="/health/live", status="200")
    assert response.status_code == 200
    assert after == before + 1


def test_http_request_duration_recorded_for_a_matched_route(client):
    before = _histogram_count(HTTP_REQUEST_DURATION_SECONDS, method="GET", path="/health/live")

    client.get("/health/live")

    after = _histogram_count(HTTP_REQUEST_DURATION_SECONDS, method="GET", path="/health/live")
    assert after == before + 1


def test_unmatched_route_is_recorded_under_a_bounded_fallback_label_not_the_raw_path(client):
    """A 404 for an arbitrary/attacker-controlled path (e.g. containing what looks like
    an ID) must not raise from the metrics middleware, must still be counted, and must
    never leak the raw path into a label -- only the bounded '/unmatched' fallback."""
    raw_path = "/this-path-does-not-exist/018f0000-0000-7000-8000-000000000099"
    before = _counter_value(HTTP_REQUESTS_TOTAL, method="GET", path=UNMATCHED_PATH_LABEL, status="404")

    response = client.get(raw_path)

    after = _counter_value(HTTP_REQUESTS_TOTAL, method="GET", path=UNMATCHED_PATH_LABEL, status="404")
    assert response.status_code == 404
    assert after == before + 1
    body = client.get("/metrics").text
    assert raw_path not in body
    assert "018f0000-0000-7000-8000-000000000099" not in body
