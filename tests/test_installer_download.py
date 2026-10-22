"""Isolated installer download tests, without internet or external packages."""
from __future__ import annotations
import hashlib
import io
import json
import ssl
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch
from installer.download import DownloadError, HTTPSRedirects, download_artifact, main, verified
from installer.manifest import Artifact, ManifestError, load_manifest

DATA = b"Odessy download fixture"
URL = "https://downloads.example.test/v1.0.0/component.zip"

class Response(io.BytesIO):
    status = 200
    def geturl(self):
        return URL

class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.artifact = Artifact("component", "1.0.0", URL, len(DATA), hashlib.sha256(DATA).hexdigest())
        self.row = {"schema_version": 1, "release": "1.0.0", "platform": "windows-x64",
                    "artifacts": [asdict(self.artifact)]}
        self.manifest = self.root / "release.json"
        self.sleep = Mock()

    def load(self, value=None):
        self.manifest.write_text(json.dumps(value if value is not None else self.row), encoding="utf-8")
        return load_manifest(self.manifest)

    def download(self, opener=None, **kwargs):
        return download_artifact(self.artifact, self.cache, open_url=opener or Mock(return_value=Response(DATA)),
                                 sleep=self.sleep, **kwargs)

    def test_manifest_is_pinned_and_counts_download_size(self):
        manifest = self.load()
        self.assertEqual(manifest.download_bytes, len(DATA))
        self.assertEqual(manifest.artifacts[0], self.artifact)

    def test_manifest_rejects_bad_artifact_fields(self):
        for key, value in (("id", "../escape"), ("version", "latest"), ("url", "http://example.test/a"),
                           ("url", "https://user:secret@example.test/a"), ("url", "file:///tmp/a"),
                           ("url", "https://example.test/a#fragment"), ("size_bytes", True),
                           ("size_bytes", -1), ("sha256", "0" * 63)):
            with self.subTest(key=key, value=value):
                row = json.loads(json.dumps(self.row))
                row["artifacts"][0][key] = value
                with self.assertRaises(ManifestError): self.load(row)

    def test_manifest_rejects_duplicate_ids_unknown_fields_and_platform(self):
        for change in (dict(artifacts=self.row["artifacts"] * 2), dict(unexpected=1),
                       dict(platform="linux-x64"), dict(schema_version=True), dict(artifacts=[])):
            with self.subTest(change=change):
                with self.assertRaises(ManifestError): self.load(dict(self.row, **change))

    def test_duplicate_json_keys_are_rejected(self):
        self.manifest.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with self.assertRaises(ManifestError): load_manifest(self.manifest)

    def test_download_is_verified_then_reused_without_network(self):
        first = self.download()
        self.assertFalse(first.cached)
        self.assertEqual(first.path.read_bytes(), DATA)
        self.assertTrue(verified(first.path, self.artifact))
        opener = Mock(side_effect=AssertionError("Network should not be contacted"))
        second = self.download(opener)
        self.assertTrue(second.cached)
        self.assertEqual(second.attempts, 0)
        self.assertEqual(list(self.cache.iterdir()), [first.path])

    def test_corrupt_cache_is_replaced_only_by_verified_content(self):
        self.cache.mkdir()
        path = self.cache / (self.artifact.sha256 + ".blob")
        path.write_bytes(b"x" * len(DATA))
        self.assertFalse(self.download().cached)
        self.assertEqual(path.read_bytes(), DATA)

    def test_bad_hash_short_and_oversized_responses_are_never_cached(self):
        for data in (b"x" * len(DATA), DATA[:-1], DATA + b"extra"):
            with self.subTest(data=data):
                with self.assertRaises(DownloadError):
                    self.download(Mock(side_effect=lambda *a, **k: Response(data)), attempts=1)
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_transient_network_error_retries_then_succeeds(self):
        opener = Mock(side_effect=[urllib.error.URLError("temporary"), Response(DATA)])
        result = self.download(opener)
        self.assertEqual(result.attempts, 2)
        self.sleep.assert_called_once_with(1)

    def test_integrity_failure_can_retry_with_fresh_file(self):
        result = self.download(Mock(side_effect=[Response(b"bad"), Response(DATA)]))
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(list(self.cache.iterdir())), 1)

    def test_retry_exhaustion_is_bounded(self):
        opener = Mock(side_effect=urllib.error.URLError("unreachable"))
        with self.assertRaises(DownloadError): self.download(opener)
        self.assertEqual(opener.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_permanent_http_error_does_not_retry_or_expose_query(self):
        secret_url = URL + "?token=private"
        opener = Mock(side_effect=urllib.error.HTTPError(secret_url, 403, "forbidden", {}, None))
        with self.assertRaises(DownloadError) as caught: self.download(opener)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(opener.call_count, 1)

    def test_http_503_retries(self):
        opener = Mock(side_effect=[urllib.error.HTTPError(URL, 503, "busy", {}, None), Response(DATA)])
        self.assertEqual(self.download(opener).attempts, 2)

    def test_certificate_error_is_not_bypassed_or_retried(self):
        opener = Mock(side_effect=urllib.error.URLError(ssl.SSLCertVerificationError("bad certificate")))
        with self.assertRaises(DownloadError): self.download(opener)
        self.assertEqual(opener.call_count, 1)

    def test_interrupted_body_removes_partial_file(self):
        class Interrupted(Response):
            def read(self, size=-1):
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.download(Mock(return_value=Interrupted(DATA)))
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_midstream_timeout_is_retried_and_partial_removed(self):
        class Interrupted(Response):
            def read(self, size=-1):
                raise TimeoutError()
        self.assertEqual(self.download(Mock(side_effect=[Interrupted(DATA), Response(DATA)])).attempts, 2)
        self.assertEqual(len(list(self.cache.iterdir())), 1)

    def test_http_redirect_is_rejected_before_following(self):
        handler = HTTPSRedirects()
        for url in ("http://example.test/a", "file:///tmp/a", "https://user:secret@example.test/a"):
            with self.subTest(url=url):
                with self.assertRaises(ManifestError):
                    handler.redirect_request(urllib.request.Request(URL), None, 302, "Found", {}, url)

    def test_link_cache_entry_cannot_escape_cache(self):
        self.cache.mkdir()
        outside = self.root / "outside"
        outside.write_bytes(DATA)
        path = self.cache / (self.artifact.sha256 + ".blob")
        # Mock the Windows reparse-point fact without requiring symlink privilege.
        with patch.object(Path, "lstat", return_value=type("Info", (), {"st_mode": 0, "st_file_attributes": 0x400})()):
            with self.assertRaises(DownloadError): verified(path, self.artifact)
        self.assertEqual(outside.read_bytes(), DATA)

    def test_progress_reports_verified_byte_count(self):
        progress = Mock()
        self.download(progress=progress)
        progress.assert_called_with("component", len(DATA), len(DATA))

    def test_cli_preview_does_not_create_cache_or_contact_network(self):
        self.load()
        with patch("builtins.print"), patch("installer.download.download_artifact") as download:
            self.assertEqual(main(["--manifest", str(self.manifest), "--cache-dir", str(self.cache)]), 0)
            download.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_invalid_manifest_cli_fails_before_cache_creation(self):
        self.manifest.write_text("{}", encoding="utf-8")
        with patch("builtins.print"):
            self.assertEqual(main(["--manifest", str(self.manifest), "--download", "--cache-dir", str(self.cache)]), 1)
        self.assertFalse(self.cache.exists())

if __name__ == "__main__":
    unittest.main()
