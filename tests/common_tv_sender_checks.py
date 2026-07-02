import importlib
import json
import unittest
from unittest import mock


class FakeResponse:
    def __init__(self, status_code, body):
        self._status_code = status_code
        self._body = body

    def getcode(self):
        return self._status_code

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class CommonTvSenderTests(unittest.TestCase):
    def test_send_to_tv_uses_shared_payload_shape(self):
        module = importlib.import_module("alert.common.tv_sender")
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse(200, '{"ok":true}')

        with mock.patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module.send_to_tv(
                "告警内容",
                mentions=["owner@kn.group"],
                bot_id="bot-1",
                api_url="https://example.com/alert",
            )

        self.assertTrue(result["success"])
        self.assertEqual(captured["url"], "https://example.com/alert")
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(
            captured["body"],
            {
                "botId": "bot-1",
                "message": "告警内容\n",
                "mentions": ["owner@kn.group"],
            },
        )


if __name__ == "__main__":
    unittest.main()
