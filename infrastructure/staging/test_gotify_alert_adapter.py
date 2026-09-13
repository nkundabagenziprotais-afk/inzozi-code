#!/usr/bin/env python3

import stat
import unittest
from datetime import datetime, timezone

import gotify_alert_adapter as adapter


class PayloadValidationTests(
    unittest.TestCase
):
    def valid_payload(self):
        return {
            "service":
                "inzozi-code-staging",
            "severity":
                "warning",
            "summary":
                "POSTGRES_BACKUP_OLDER_THAN_6H",
            "timestamp":
                datetime.now(
                    timezone.utc
                ).isoformat(),
        }

    def test_valid_warning(self):
        result = adapter.validate_payload(
            self.valid_payload()
        )

        self.assertEqual(
            result["severity"],
            "warning",
        )

    def test_valid_critical(self):
        payload = self.valid_payload()

        payload["severity"] = (
            "critical"
        )

        payload["summary"] = (
            "HTTPS_READY_FAIL;"
            "WARN:ROOT_DISK_GE_80"
        )

        result = adapter.validate_payload(
            payload
        )

        self.assertEqual(
            result["severity"],
            "critical",
        )

    def test_extra_field_rejected(self):
        payload = self.valid_payload()

        payload["authorization"] = (
            "forbidden"
        )

        with self.assertRaises(
            adapter.ValidationError
        ):
            adapter.validate_payload(
                payload
            )

    def test_wrong_service_rejected(self):
        payload = self.valid_payload()
        payload["service"] = "other"

        with self.assertRaises(
            adapter.ValidationError
        ):
            adapter.validate_payload(
                payload
            )

    def test_info_rejected(self):
        payload = self.valid_payload()
        payload["severity"] = "info"

        with self.assertRaises(
            adapter.ValidationError
        ):
            adapter.validate_payload(
                payload
            )

    def test_spaces_rejected(self):
        payload = self.valid_payload()
        payload["summary"] = (
            "arbitrary free text"
        )

        with self.assertRaises(
            adapter.ValidationError
        ):
            adapter.validate_payload(
                payload
            )

    def test_newline_rejected(self):
        payload = self.valid_payload()

        payload["summary"] = (
            "HTTPS_READY_FAIL\nSECRET"
        )

        with self.assertRaises(
            adapter.ValidationError
        ):
            adapter.validate_payload(
                payload
            )

    def test_timezone_required(self):
        payload = self.valid_payload()

        payload["timestamp"] = (
            "2026-09-10T12:00:00"
        )

        with self.assertRaises(
            adapter.ValidationError
        ):
            adapter.validate_payload(
                payload
            )


class TranslationTests(
    unittest.TestCase
):
    def test_warning_priority(self):
        message = (
            adapter.to_gotify_message(
                {
                    "service":
                        "inzozi-code-staging",
                    "severity":
                        "warning",
                    "summary":
                        "ROOT_DISK_GE_80",
                    "timestamp":
                        "2026-09-10T10:00:00+00:00",
                }
            )
        )

        self.assertEqual(
            message["priority"],
            5,
        )

        self.assertEqual(
            message["title"],
            "Inzozi Code staging WARNING",
        )

    def test_critical_priority(self):
        message = (
            adapter.to_gotify_message(
                {
                    "service":
                        "inzozi-code-staging",
                    "severity":
                        "critical",
                    "summary":
                        "HTTPS_READY_FAIL",
                    "timestamp":
                        "2026-09-10T10:00:00+00:00",
                }
            )
        )

        self.assertEqual(
            message["priority"],
            10,
        )


class EndpointTests(
    unittest.TestCase
):
    def test_loopback_url(self):
        self.assertEqual(
            adapter.validate_local_gotify_url(
                "http://127.0.0.1:8088"
            ),
            "http://127.0.0.1:8088",
        )

    def test_public_host_rejected(self):
        with self.assertRaises(
            adapter.ValidationError
        ):
            adapter.validate_local_gotify_url(
                "https://example.com"
            )

    def test_wrong_port_rejected(self):
        with self.assertRaises(
            adapter.ValidationError
        ):
            adapter.validate_local_gotify_url(
                "http://127.0.0.1:80"
            )

    def test_url_credentials_rejected(self):
        with self.assertRaises(
            adapter.ValidationError
        ):
            adapter.validate_local_gotify_url(
                "http://user:pass@127.0.0.1:8088"
            )


class SecretMetadataTests(
    unittest.TestCase
):
    def test_root_0600_allowed(self):
        self.assertTrue(
            adapter.secret_metadata_ok(
                0,
                stat.S_IFREG | 0o600,
            )
        )

    def test_root_0400_allowed(self):
        self.assertTrue(
            adapter.secret_metadata_ok(
                0,
                stat.S_IFREG | 0o400,
            )
        )

    def test_0640_rejected(self):
        self.assertFalse(
            adapter.secret_metadata_ok(
                0,
                stat.S_IFREG | 0o640,
            )
        )

    def test_nonroot_rejected(self):
        self.assertFalse(
            adapter.secret_metadata_ok(
                501,
                stat.S_IFREG | 0o600,
            )
        )

    def test_symlink_rejected(self):
        self.assertFalse(
            adapter.secret_metadata_ok(
                0,
                stat.S_IFLNK | 0o600,
            )
        )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
