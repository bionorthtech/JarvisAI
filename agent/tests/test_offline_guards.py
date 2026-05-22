"""3A — offline guard tests.

Verifies the three new guards land without breaking the happy path:
  - web_search refuses when security.internet_access=False
  - WebhookAdapter refuses non-loopback URLs when offline
  - WebhookAdapter accepts loopback URLs even when offline
"""
import unittest
from unittest.mock import patch, MagicMock

from agent.core import config as config_mod
from agent.core.adapters import WebhookAdapter, _is_loopback_url


def _cfg(internet: bool):
    """Build a JarvisConfig-shaped mock with the requested
    internet_access setting."""
    sec = MagicMock()
    sec.internet_access = internet
    fake = MagicMock()
    fake.security = sec
    return fake


class TestLoopbackURLDetector(unittest.TestCase):
    def test_loopback_variants(self):
        for url in (
            "http://127.0.0.1:8000/x",
            "http://localhost/x",
            "http://[::1]/x",
            "http://127.0.0.5/y",
        ):
            self.assertTrue(_is_loopback_url(url), url)

    def test_non_loopback_rejected(self):
        for url in (
            "https://example.com/x",
            "http://192.168.1.5/x",
            "https://api.openai.com/v1",
        ):
            self.assertFalse(_is_loopback_url(url), url)


class TestWebSearchOfflineGuard(unittest.IsolatedAsyncioTestCase):
    async def test_offline_returns_explicit_error_without_network(self):
        from plugins.web_search import plugin as ws
        with patch.object(config_mod, "config", _cfg(internet=False)):
            # If the guard fails, urlopen would be called — guard ensures
            # we never get there. Patch urlopen so the test fails loud if
            # the guard is bypassed.
            with patch("urllib.request.urlopen",
                       side_effect=AssertionError("urlopen called!")):
                result = await ws.web_search("anything")
        self.assertIn("disabled", result.lower())
        self.assertIn("offline", result.lower())


class TestWebhookOfflineGuard(unittest.TestCase):
    def _adapter(self, url: str) -> WebhookAdapter:
        return WebhookAdapter(
            adapter_id="t", kind="webhook",
            config={"url": url, "enabled": True},
        )

    def test_non_loopback_blocked_when_offline(self):
        a = self._adapter("https://example.com/hook")
        with patch.object(config_mod, "config", _cfg(internet=False)):
            with patch("urllib.request.urlopen",
                       side_effect=AssertionError("urlopen called!")):
                out = a.dispatch("post", {"x": 1})
        self.assertIn("error", out)
        self.assertIn("loopback", out["error"])

    def test_loopback_allowed_when_offline(self):
        a = self._adapter("http://127.0.0.1:9999/hook")
        with patch.object(config_mod, "config", _cfg(internet=False)):
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = lambda self: self
            mock_resp.__exit__ = lambda *a: None
            with patch("urllib.request.urlopen", return_value=mock_resp):
                out = a.dispatch("post", {"x": 1})
        self.assertEqual(out.get("ok"), True)


if __name__ == "__main__":
    unittest.main()
