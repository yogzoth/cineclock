import httpx
import pytest

from cineclock.scrapers import PatientClient


def client(answers, waits):
    calls = iter(answers)

    def handler(request):
        answer = next(calls)
        if isinstance(answer, Exception):
            raise answer
        return answer

    patient = PatientClient(transport=httpx.MockTransport(handler))
    patient.sleep = waits.append
    return patient


def test_a_busy_site_is_asked_again_after_its_retry_after():
    waits = []
    c = client(
        [httpx.Response(429, headers={"Retry-After": "5"}), httpx.Response(200, text="ok")], waits
    )
    assert c.get("https://x/").text == "ok"
    assert waits == [5.0]


def test_it_gives_up_after_two_more_tries_and_returns_the_last_answer():
    waits = []
    c = client([httpx.Response(503)] * 3, waits)
    assert c.get("https://x/").status_code == 503
    assert waits == [2.0, 6.0]


def test_a_timeout_is_retried_then_raised():
    waits = []
    timeout = httpx.ReadTimeout("slow")
    c = client([timeout, httpx.Response(200)], waits)
    assert c.get("https://x/").status_code == 200
    c = client([timeout, timeout, timeout], [])
    with pytest.raises(httpx.ReadTimeout):
        c.get("https://x/")


def test_a_missing_page_is_not_retried():
    waits = []
    assert client([httpx.Response(404)], waits).get("https://x/").status_code == 404
    assert waits == []
