"""Synthetic repositories and payloads; no deployment data or network access."""

import ipaddress
import subprocess
import tempfile
import unittest
from pathlib import Path

from check_hygiene import audit, inspect


class HygieneTests(unittest.TestCase):
    def test_network_addresses_and_user_agent_are_distinguished(self):
        for value in (0x08080808, 0x0A010203):
            payload = str(ipaddress.ip_address(value)).encode()
            self.assertTrue(inspect("example.txt", payload))
        self.assertEqual(inspect("example.txt", b"127.0.0.1 0.0.0.0 192.0.2.7"), [])
        self.assertEqual(inspect("example.txt", b"Chrome/146.0.0.0"), [])

    def test_personal_paths_and_private_key_are_rejected_without_disclosure(self):
        payloads = [
            "/" + "home/synthetic/project",
            "/" + "root/.ssh/key",
            "/" + "Users/synthetic/project",
            "C:" + "\\Users\\synthetic\\project",
            "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                problems = inspect("example.txt", payload.encode())
                self.assertTrue(problems)
                self.assertNotIn(payload, "\n".join(problems))
        self.assertEqual(inspect("example.txt", b"/app /run/secrets /usr/bin/env"), [])

    def test_private_artifacts_are_rejected_even_when_force_added(self):
        for name in (
            ".env",
            ".local/data",
            "frontend/test-results/trace.zip",
            "db.dump",
        ):
            self.assertTrue(inspect(name, b""))
        self.assertEqual(inspect(".env.example", b"PUBLIC_ORIGIN="), [])

    def test_staged_scan_reads_index_instead_of_clean_working_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                subprocess.run(
                    ["git", *args], cwd=root, check=True, capture_output=True
                )

            git("init", "-q")
            (root / ".gitignore").write_text(
                ".env\n.local/\n*.pem\n*.swp\n", encoding="utf-8"
            )
            source = root / "example.txt"
            source.write_text("/" + "home/synthetic/project", encoding="utf-8")
            git("add", ".")
            source.write_text("anonymous", encoding="utf-8")
            self.assertFalse(audit(root)[1])
            self.assertTrue(audit(root, staged=True)[1])
            git("add", "example.txt")
            self.assertFalse(audit(root, staged=True)[1])
            # An ignore rule does not make an already staged secret safe.
            (root / ".env").write_text("PRIVATE=synthetic", encoding="utf-8")
            git("add", "-f", ".env")
            self.assertTrue(audit(root, staged=True)[1])


if __name__ == "__main__":
    unittest.main()
