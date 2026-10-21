"""Verified download cache. Never unpacks, installs or executes downloaded files."""
from __future__ import annotations
import argparse
import hashlib
import http.client
import os
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from installer.manifest import Artifact, ManifestError, load_manifest, validate_https_url

class DownloadError(Exception):
    pass

class IntegrityError(DownloadError):
    pass

class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def open_https(request, *, timeout):
    return urllib.request.build_opener(HTTPSRedirects()).open(request, timeout=timeout)

def _reject_link(path: Path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise DownloadError("Cache entries must not be symbolic links or reparse points.")

def verified(path: Path, artifact: Artifact) -> bool:
    _reject_link(path)
    if not path.exists():
        return False
    if not path.is_file():
        raise DownloadError("Cache entry is not a regular file.")
    if path.stat().st_size != artifact.size_bytes:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == artifact.sha256

@dataclass(frozen=True)
class DownloadResult:
    path: Path
    cached: bool
    attempts: int

def download_artifact(artifact: Artifact, cache_dir: Path, *, attempts: int = 3,
                      timeout: float = 30.0, open_url: Callable = open_https,
                      sleep: Callable[[float], None] = time.sleep,
                      progress: Callable[[str, int, int], None] | None = None) -> DownloadResult:
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError("attempts must be between 1 and 5.")
    if not 0 < timeout <= 300:
        raise ValueError("timeout must be positive and at most 300 seconds.")
    _reject_link(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / (artifact.sha256 + ".blob")
    if verified(destination, artifact):
        return DownloadResult(destination, True, 0)
    for attempt in range(1, attempts + 1):
        partial = None
        try:
            request = urllib.request.Request(artifact.url, headers={"User-Agent": "OdessyInstaller/0.1"})
            with open_url(request, timeout=timeout) as response:
                validate_https_url(response.geturl())
                if response.status != 200:
                    raise DownloadError("Expected a complete HTTP 200 response.")
                digest, received = hashlib.sha256(), 0
                with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".odessy-", suffix=".part", delete=False) as stream:
                    partial = Path(stream.name)
                    while True:
                        chunk = response.read(min(1024 * 1024, artifact.size_bytes - received + 1))
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > artifact.size_bytes:
                            raise IntegrityError("Download exceeded the release size.")
                        digest.update(chunk)
                        stream.write(chunk)
                        if progress:
                            progress(artifact.id, received, artifact.size_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())
                if received != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
                    raise IntegrityError("Downloaded size or SHA-256 does not match the release manifest.")
            _reject_link(destination)
            os.replace(partial, destination)
            partial = None
            return DownloadResult(destination, False, attempt)
        except urllib.error.HTTPError as error:
            retry = error.code in {408, 429} or 500 <= error.code <= 599
            reason = f"HTTP {error.code}"
            error.close()
        except (ssl.SSLCertVerificationError, ManifestError):
            retry, reason = False, "TLS certificate or redirect validation failed"
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, ssl.SSLError) as error:
            retry = not isinstance(getattr(error, "reason", None), ssl.SSLCertVerificationError)
            reason = "Network connection failed; check connectivity, proxy and TLS settings"
        except IntegrityError as error:
            retry, reason = True, str(error)
        except OSError as error:
            raise DownloadError("Cannot read or write download cache; check permissions and free space.") from error
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)
        if not retry or attempt == attempts:
            raise DownloadError(f"{artifact.id}: {reason} (attempt {attempt}/{attempts}).")
        sleep(min(2 ** (attempt - 1), 8))
    raise AssertionError("Unreachable")

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Trusted local release manifest.")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "Odessy" / "downloads")
    parser.add_argument("--download", action="store_true", help="Confirm downloading the listed artifacts.")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        print(f"Release: {manifest.release}; {len(manifest.artifacts)} file(s); {manifest.download_bytes} bytes")
        for artifact in manifest.artifacts:
            print(f"- {artifact.id}: {artifact.version}, {artifact.size_bytes} bytes, SHA-256 {artifact.sha256}")
        if not args.download:
            print("PREVIEW ONLY. Add --download to fetch and verify files; nothing will be installed.")
            return 0
        last_percent = {}
        def show_progress(name, done, total):
            percent = done * 100 // total
            if percent == 100 or percent // 10 > last_percent.get(name, -1) // 10:
                print(f"[DOWNLOAD] {name}: {percent}% ({done}/{total} bytes)", flush=True)
                last_percent[name] = percent
        for artifact in manifest.artifacts:
            result = download_artifact(artifact, args.cache_dir, progress=show_progress)
            print(f"[OK] {artifact.id}: {'verified cache' if result.cached else 'download verified'}; {result.path}")
        print("Download stage complete. No files were unpacked, installed or executed.")
        return 0
    except (ManifestError, DownloadError, OSError, ValueError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Download cancelled; incomplete files removed.", file=sys.stderr)
        return 130

if __name__ == "__main__":
    raise SystemExit(main())
