"""error_log 远程响应格式化。"""

from __future__ import annotations

import unittest

from app.utils.error_log import format_error_detail, format_http_response_body


class ErrorLogTests(unittest.TestCase):
    def test_format_http_response_body_json(self) -> None:
        text = '{"code":500,"message":"internal error"}'
        out = format_http_response_body(text)
        self.assertIn("internal error", out)

    def test_http_error_includes_body(self) -> None:
        try:
            import requests

            r = requests.models.Response()
            r.status_code = 502
            r._content = b'{"detail":"bad gateway"}'
            r.url = "http://example/api"
            exc = requests.HTTPError("502", response=r)
            detail = format_error_detail("fetch_mode", exc)
            self.assertIn("502", detail)
            self.assertIn("bad gateway", detail)
        except ImportError:
            self.skipTest("requests not installed")


if __name__ == "__main__":
    unittest.main()
