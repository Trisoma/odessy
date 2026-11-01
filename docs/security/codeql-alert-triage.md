# CodeQL alert triage

2026/11/1 - Internal log

Branch: `fix/codeql-security-alerts`.

- #1: monitoring workflow now grants only `contents: read`.
- #2 and #3: MCP HTTP errors return a fixed plain-text response, without exception text.
- #4: the diagnostic JavaScript extraction test now matches script tags case-insensitively; this is extraction of a committed fixture, not HTML sanitization.
- #5: model repair debug logs omit payloads, including tool arguments and final responses. Pattern redaction cannot identify every opaque secret.
- #6: manual API startup requires a caller-configured token and never prints its value. Desktop-owned startup retains its existing private credential channel. See the README for manual setup.
- #7: the installer validates literal loopback endpoints and now explicitly prevents an empty bind host. Other loopback addresses retain their meaning.
- #8 and #9: doctor tests compare complete expected instructions, rather than testing domain substrings. They are not URL allowlists.
- #10: false positive. `Identity::bundle()` exports only public CA/certificate material, installation ID, and SHA-256 fingerprint. `export_identity` prints the public certificate fingerprint for out-of-band verification, not a private key. `identity::tests::bundle_carries_no_private_key_and_matches_the_certificate` verifies that the serialized bundle excludes `key_pem`. Removing this fingerprint would damage the enrollment verification flow.

Scanner closure for changed code requires a new CodeQL run on the updated branch.

Alert #10 was dismissed on GitHub as a false positive with the evidence above.
Alerts #1–#9 await a scan of these changes; no suppressions were added.

## Verification

Commands run from the worktree root unless specified otherwise. Python used the
existing environment with `PYTHONPATH` pointing to this worktree's `src` directory.

```text
python -m pytest -q tests/test_security_logging.py tests/test_doctor.py tests/test_ollama_model_contract.py tests/test_output_spill.py tests/test_application_api.py tests/test_installer_ollama.py tests/test_installer_ollama_runtime.py tests/test_model_credentials.py --tb=short
154 passed, 2 skipped, 30 subtests passed in 16.92s

python tool/check_desktop_contracts.py
Validated 107 runtime settings; unavailable controls do not count as implemented.

cd tools
npm run typecheck
tsc --noEmit (exit 0)
npm test
tests 206; pass 201; fail 0; skipped 5

cargo test --manifest-path native/odessy-link/Cargo.toml --locked bundle_carries_no_private_key_and_matches_the_certificate
1 passed; 0 failed; 75 filtered out

git diff --check
(no output; exit 0)
```

Not run: the full repository suite, Linux execution, or a fresh CodeQL scan of
this uncommitted branch. Node's platform-dependent skips remain skips, not passes.
