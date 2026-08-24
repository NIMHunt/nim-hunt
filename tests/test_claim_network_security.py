from __future__ import annotations

import unittest

from starlette.requests import Request

import claim_network_security


class ClaimNetworkSecurityTest(unittest.TestCase):
    def _scope(self, *, headers=None, client=("10.0.0.8", 1234)):
        return {
            "type": "http",
            "method": "POST",
            "path": "/api/security/challenge",
            "headers": [
                (str(key).lower().encode("latin-1"), str(value).encode("latin-1"))
                for key, value in (headers or {}).items()
            ],
            "client": client,
            "server": ("testserver", 80),
            "scheme": "https",
            "query_string": b"",
        }

    def test_scope_prefers_valid_x_real_ip_over_proxy_peer(self):
        scope = self._scope(
            headers={
                "x-real-ip": "203.0.113.42",
                "x-forwarded-for": "203.0.113.42, 100.64.0.2",
            },
            client=("100.64.0.3", 443),
        )
        self.assertEqual(claim_network_security.scope_ip(scope), "203.0.113.42")

    def test_scope_does_not_use_rightmost_x_forwarded_for_proxy(self):
        scope = self._scope(
            headers={"x-forwarded-for": "203.0.113.42, 100.64.0.2"},
            client=("100.64.0.3", 443),
        )
        self.assertEqual(claim_network_security.scope_ip(scope), "100.64.0.3")

    def test_malformed_x_real_ip_falls_back_to_peer(self):
        scope = self._scope(
            headers={"x-real-ip": "not-an-ip"},
            client=("192.0.2.10", 443),
        )
        self.assertEqual(claim_network_security.scope_ip(scope), "192.0.2.10")

    def test_ipv6_is_normalised(self):
        scope = self._scope(
            headers={"x-real-ip": "2001:0db8:0000:0000:0000:0000:0000:0001"},
        )
        self.assertEqual(claim_network_security.scope_ip(scope), "2001:db8::1")

    def test_request_helper_uses_same_rule(self):
        request = Request(
            self._scope(
                headers={"x-real-ip": "198.51.100.7"},
                client=("100.64.0.4", 443),
            )
        )
        self.assertEqual(claim_network_security.request_ip(request), "198.51.100.7")


if __name__ == "__main__":
    unittest.main()
