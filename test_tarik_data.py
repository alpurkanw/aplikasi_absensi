from tarik_data import AgentEngine


def test_check_web_connection_returns_connected(monkeypatch):
    class DummyResponse:
        status_code = 200

    def fake_get(url, timeout):
        assert url
        assert timeout == 5
        return DummyResponse()

    monkeypatch.setattr("tarik_data.requests.get", fake_get)

    connected, status_code, details = AgentEngine.check_web_connection(timeout=5)

    assert connected is True
    assert status_code == 200
    assert details is not None
