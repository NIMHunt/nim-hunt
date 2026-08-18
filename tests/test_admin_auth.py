from __future__ import annotations

import unittest

import admin_auth


class AdminAuthTests(unittest.TestCase):
    def test_password_hash_round_trip(self):
        password = "correct horse battery staple admin"
        encoded = admin_auth.hash_admin_password(password, salt=b"0123456789abcdef")

        self.assertTrue(admin_auth.verify_admin_password(password, encoded))
        self.assertFalse(admin_auth.verify_admin_password("wrong password entirely", encoded))
        self.assertNotIn(password, encoded)
        self.assertTrue(encoded.startswith("scrypt$16384$8$1$"))

    def test_password_hash_rejects_short_password(self):
        with self.assertRaisesRegex(ValueError, "at least 16"):
            admin_auth.hash_admin_password("too-short")

    def test_malformed_password_hash_fails_closed(self):
        for encoded in ("", "sha256$abc", "scrypt$bad", "scrypt$16384$8$1$bad$bad"):
            with self.subTest(encoded=encoded):
                self.assertFalse(admin_auth.verify_admin_password("anything long enough", encoded))

    def test_signed_session_and_csrf(self):
        token, created = admin_auth.create_admin_session(now=1_000)
        read = admin_auth.read_admin_session(token, now=1_001)

        self.assertIsNotNone(read)
        self.assertEqual(read.expires_at, 1_000 + admin_auth.ADMIN_SESSION_SECONDS)
        self.assertTrue(admin_auth.verify_csrf(read, created.csrf_token))
        self.assertFalse(admin_auth.verify_csrf(read, "not-the-token"))

    def test_session_tampering_and_expiry_fail_closed(self):
        token, _session = admin_auth.create_admin_session(now=5_000)
        self.assertIsNone(admin_auth.read_admin_session(token + "x", now=5_001))
        self.assertIsNone(
            admin_auth.read_admin_session(
                token,
                now=5_000 + admin_auth.ADMIN_SESSION_SECONDS,
            )
        )


if __name__ == "__main__":
    unittest.main()
