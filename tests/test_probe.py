from grid_watch import probe


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append(kwargs)
        return None


def test_build_session_api_injects_timeout_and_user_agent():
    api = probe.build_session_api("Mozilla/5.0 (X11; Linux x86_64)")
    assert "Mozilla" in api.agent_identifier
    fake = FakeSession()
    api.session = fake
    probe.patch_session_timeout(api, timeout=20)
    api.session.request("GET", "http://example.invalid")
    assert fake.calls[0]["timeout"] == 20


def test_patch_session_timeout_is_idempotent():
    api = probe.build_session_api("UA")
    fake = FakeSession()
    api.session = fake
    probe.patch_session_timeout(api, timeout=20)
    probe.patch_session_timeout(api, timeout=20)
    api.session.request("GET", "http://example.invalid")
    assert len(fake.calls) == 1
    assert fake.calls[0]["timeout"] == 20
