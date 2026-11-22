/**
 * The Node output pipeline: redact -> retain -> spill.
 *
 * These are ports of `redaction.py`, `output_retention.py`, and `spill.py`, so the
 * tests pin the *semantics the Python modules document* rather than whatever this
 * implementation happened to do. The four that matter most:
 *
 * - a secret never reaches the spill file (redaction runs before persistence),
 * - the omitted byte count is measured after boundary trimming, not derived from
 *   the budget,
 * - a storage failure returns the original rather than a lossy cut,
 * - redaction stays linear on the input shape that made the original quadratic.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { prepareOutput } from "../src/output/prepare.js";
import { looksLikeSecretName, REDACTED, redactText } from "../src/output/redaction.js";
import { formatRetentionNotice, TextRetainer } from "../src/output/retention.js";
import { SpillStore } from "../src/output/spill.js";

function makeWorkspace(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "odessy-output-"));
}

// --------------------------------------------------------------------------
// Redaction
// --------------------------------------------------------------------------

test("credential names survive a redacted assignment", () => {
  const redacted = redactText("API_KEY=sk-abcdef1234567890");

  assert.doesNotMatch(redacted, /sk-abcdef1234567890/);
  // The NAME is kept so a reader can still tell which credential was involved.
  assert.match(redacted, /API_KEY/);
  assert.match(redacted, new RegExp(REDACTED));
});

test("well-known token shapes are caught without a telling name", () => {
  for (const token of [
    "sk-abcdefghijklmnopqrst",
    "ghp_abcdefghijklmnopqrst",
    "github_pat_abcdefghijklmnopqrstuv",
    "AKIAIOSFODNN7EXAMPLE",
    "xoxb-1234567890abcdef",
    "-----BEGIN RSA PRIVATE KEY-----",
  ]) {
    assert.doesNotMatch(redactText(token), new RegExp(token.slice(0, 12)), token);
  }
});

test("short and innocuous assignments are left alone", () => {
  // A loose rule here would corrupt legitimate output, which is why the value
  // floor and the name check both exist.
  for (const text of ["TOKEN=1", "DEBUG=on", "PORT=8080", "NAME=production"]) {
    assert.equal(redactText(text), text, text);
  }
});

test("redaction is idempotent", () => {
  // Applied at more than one boundary, so applying it twice must be safe.
  const once = redactText("API_KEY=sk-abcdef1234567890");

  assert.equal(redactText(once), once);
});

test("secret name terms match case-insensitively as substrings", () => {
  for (const name of ["API_KEY", "MY_APP_API_KEY", "apiKey", "apikey", "GITHUB_TOKEN"]) {
    assert.equal(looksLikeSecretName(name), true, name);
  }
  for (const name of ["PATH", "HOSTNAME", "DEBUG"]) {
    assert.equal(looksLikeSecretName(name), false, name);
  }
});

test("redaction stays linear on a long unbroken token", { timeout: 2000 }, () => {
  // The bounded quantifiers are a hot-path correctness constraint, not style: an
  // unbounded name group makes this input quadratic and took the Python original
  // 33 seconds on 50 000 characters. Terminal output has exactly this shape.
  const line = "A".repeat(50_000);

  const started = Date.now();
  redactText(line);
  const elapsed = Date.now() - started;

  assert.ok(elapsed < 1000, `redaction took ${elapsed}ms, which suggests a backtracking blowup`);
});

// --------------------------------------------------------------------------
// Retention
// --------------------------------------------------------------------------

test("text within budget is returned unchanged", () => {
  const result = new TextRetainer({ headBytes: 10, tailBytes: 10 }).retain("short");

  assert.equal(result.text, "short");
  assert.equal(result.truncated, false);
  assert.equal(result.omitted_bytes.count, 0);
  assert.equal(result.kept_prefix_bytes, 5);
});

test("both ends are kept and the omission is measured, not derived", () => {
  // Four 4-byte emoji: 16 bytes. With a 6-byte head budget, boundary trimming drops
  // the two bytes of the second emoji that would have been half a codepoint. So 4
  // bytes are kept, not 6 — and the omission is 12, not 16-6=10. A notice computed
  // from the budget would overstate what was kept and so be false.
  const result = new TextRetainer({ headBytes: 6, tailBytes: 0 }).retain("😀😀😀😀");

  assert.equal(result.truncated, true);
  assert.equal(result.kept_prefix_bytes, 4);
  assert.equal(result.kept_suffix_bytes, 0);
  assert.equal(result.omitted_bytes.count, 12);
  assert.equal(result.omitted_bytes.count, result.total_bytes - 4);
  assert.equal(result.text, "😀");
});

test("a trimmed multibyte boundary never yields a replacement character", () => {
  const text = "😀".repeat(200);

  for (const headBytes of [1, 2, 3, 5, 7, 11, 13]) {
    const result = new TextRetainer({ headBytes, tailBytes: 0 }).retain(text);
    assert.doesNotMatch(result.text, /\uFFFD/, `headBytes=${headBytes}`);
  }
});

test("both ends are retained when the text is much larger than the budget", () => {
  const result = new TextRetainer({ headBytes: 4, tailBytes: 4 }).retain("HEADER-middling-TRAILER");

  assert.equal(result.truncated, true);
  assert.match(result.text, /HEAD/);
  assert.match(result.text, /ILER/);
});

test("the notice states the exact omission and carries recovery when given", () => {
  const result = new TextRetainer({ headBytes: 2, tailBytes: 2 }).retain("abcdefghijklmnop");

  const notice = formatRetentionNotice(result, {
    unitLabel: "stdout",
    recovery: "Full output: .odessy/spill/x/y.txt",
  });

  assert.match(notice, /stdout truncated/);
  assert.match(notice, new RegExp(`omitted ${result.omitted_bytes.count} bytes`));
  assert.match(notice, /Full output: /);
  assert.equal(formatRetentionNotice({ ...result, truncated: false }), "");
});

// --------------------------------------------------------------------------
// Spill
// --------------------------------------------------------------------------

test("a spilled payload is byte-identical and content-addressed", () => {
  const workspace = makeWorkspace();
  const store = new SpillStore(workspace);
  const text = "x".repeat(5000);

  const first = store.saveText(text, "stdout");
  const second = store.saveText(text, "stdout");

  assert.ok(first);
  assert.ok(second);
  // Content-addressed: identical output resolves to the same target, which is why
  // the atomic write must use a random temp name.
  assert.equal(first.relative_path, second.relative_path);
  assert.equal(fs.readFileSync(first.path, "utf8"), text);

  const other = store.saveText("different", "stdout");
  assert.ok(other);
  assert.notEqual(other.digest, first.digest);
});

test("the locator path stays inside the workspace", () => {
  const workspace = makeWorkspace();

  // The label is caller-supplied, so it must not be able to introduce a separator
  // or a `..` segment.
  const locator = new SpillStore(workspace).saveText("payload", "../../evil");

  assert.ok(locator);
  assert.ok(fs.realpathSync(locator.path).startsWith(fs.realpathSync(workspace)));
  assert.match(locator.relative_path, /^\.odessy\/spill\//);
  assert.doesNotMatch(locator.relative_path, /\.\./);
});

test("the line count treats a trailing newline as a terminator", () => {
  // Python's `splitlines()` does not produce a trailing empty element.
  const workspace = makeWorkspace();
  const store = new SpillStore(workspace);

  assert.equal(store.saveText("a\nb\n", "stdout")?.line_count, 2);
  assert.equal(store.saveText("a\nb", "stdout")?.line_count, 2);
  assert.equal(store.saveText("", "stdout")?.line_count, 0);
});

// --------------------------------------------------------------------------
// Prepare: the ordering and the failure branch
// --------------------------------------------------------------------------

test("a secret never reaches the spill file", () => {
  // This is the ordering rule the whole pipeline exists for. Redacting only the
  // model-facing text would leave the credential persisted on disk, where it
  // cannot be recalled.
  const workspace = makeWorkspace();
  const store = new SpillStore(workspace);
  const secret = "sk-abcdefghijklmnopqrstuvwx";

  const prepared = prepareOutput(`HEAD${"y".repeat(200_000)}API_KEY=${secret}`, {
    label: "stdout",
    maxChars: 1000,
    spill: store,
  });

  assert.equal(prepared.truncated, true);
  assert.ok(prepared.spill);
  assert.doesNotMatch(prepared.text, new RegExp(secret));
  assert.doesNotMatch(fs.readFileSync(prepared.spill.path, "utf8"), new RegExp(secret));
});

test("a spilling failure returns the original rather than a lossy cut", () => {
  // A storage failure must not become silent data loss. The cost is a large
  // observation, and that cost is the caller's to weigh, not this function's to
  // hide — so `truncated` is false and the text is complete.
  const workspace = makeWorkspace();
  // Block the spill directory by putting a FILE where the directory must go.
  fs.writeFileSync(path.join(workspace, ".odessy"), "not a directory");

  const text = "z".repeat(200_000);
  const prepared = prepareOutput(text, {
    label: "stdout",
    maxChars: 1000,
    spill: new SpillStore(workspace),
  });

  assert.equal(prepared.truncated, false);
  assert.equal(prepared.spill, null);
  assert.equal(prepared.text, text);
});

test("short output is passed through untouched", () => {
  const workspace = makeWorkspace();

  const prepared = prepareOutput("hello", {
    label: "stdout",
    maxChars: 1000,
    spill: new SpillStore(workspace),
  });

  assert.deepEqual(prepared, { text: "hello", truncated: false, spill: null });
});
