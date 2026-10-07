/**
 * Secret redaction at Odessy's persistence and model-facing boundaries.
 *
 * TypeScript mirror of `odessy/redaction.py`. The two must agree on what counts
 * as a credential, because the same vocabulary answers both questions: *may this
 * variable reach a child process?* and *may this value reach the model or a file?*
 *
 * **This is not a complete security guarantee.** Detection is name-based plus a
 * small set of well-known token shapes. A secret printed in a form matching
 * neither — an opaque value in an arbitrarily-named variable, a credential echoed
 * by a tool that does not name it — is not caught. It is a best-effort boundary
 * that raises the cost of accidental leakage, not a proof of safety.
 *
 * The ordering rule that matters: **redact before persisting, not before sending.**
 * Redacting on the way out of the runtime leaves the secret on disk in the spill
 * file, and by then it is too late to recall.
 */

/**
 * Placeholder written in place of a redacted value.
 *
 * Keeps the name so a reader can still reason about *which* credential was
 * involved — the same "a key name is safe to print, a value is not" line the
 * upstream project draws.
 */
export const REDACTED = "<redacted>";

/**
 * Variable and field names treated as credential-bearing.
 *
 * Matched as a case-insensitive substring, so `MY_APP_API_KEY`, `apiKey`, and
 * `apikey` are all covered by `API_KEY`.
 */
export const SECRET_NAME_TERMS: readonly string[] = [
  "APIKEY",
  "API_KEY",
  "AWS_",
  "AZURE_",
  "CLIENT_SECRET",
  "CREDENTIAL",
  "GITHUB_TOKEN",
  "HF_TOKEN",
  "HUGGINGFACE",
  "OPENAI_",
  "PASSWD",
  "PASSWORD",
  "PASSPHRASE",
  "PRIVATE_KEY",
  "PRIVATE",
  "SECRET",
  "TOKEN",
];

/**
 * Well-known credential shapes, caught even when the surrounding name is
 * innocuous. Deliberately short and high-confidence: a loose pattern here would
 * corrupt legitimate output.
 */
const SECRET_VALUE_PATTERNS: readonly RegExp[] = [
  /\bsk-[A-Za-z0-9_-]{16,}/g,
  /\bsk-ant-[A-Za-z0-9_-]{16,}/g,
  /\bgh[pousr]_[A-Za-z0-9]{16,}/g,
  /\bgithub_pat_[A-Za-z0-9_]{20,}/g,
  /\bAKIA[0-9A-Z]{16}\b/g,
  /\bxox[abpsr]-[A-Za-z0-9-]{10,}/g,
  /\bAIza[0-9A-Za-z_-]{30,}/g,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/g,
];

/**
 * `NAME=value`, `NAME: value`, `"name": "value"`, optionally quoted.
 *
 * Both quantifiers are BOUNDED, and that is load-bearing rather than cosmetic.
 * With an unbounded `name` group, a long run of word characters containing no
 * separator forces the engine to retry every split point at every offset —
 * quadratic behaviour that made the Python original take 33 seconds on a
 * 50 000-character line of plain text. Terminal output produces exactly that
 * shape (base64 blobs, progress bars, long log lines), so this is a hot-path
 * correctness constraint, not a style choice. A bounded repeat is linear.
 *
 * The `value` group is bounded too, which additionally caps how much text one
 * match can claim.
 */
const ASSIGNMENT =
  /(?<nq>["']?)(?<name>[A-Za-z_][A-Za-z0-9_]{0,63})\k<nq>\s*(?<sep>=|:)\s*(?<vq>["']?)(?<value>[^\s"',;]{6,4096})\k<vq>/g;

/** Values shorter than this are left alone, so `TOKEN=1` and `DEBUG=on` survive. */
const MIN_SECRET_VALUE_LEN = 6;

/** Whether a variable or field name is treated as credential-bearing. */
export function looksLikeSecretName(name: string): boolean {
  const upper = name.toUpperCase();
  return SECRET_NAME_TERMS.some((term) => upper.includes(term));
}

/**
 * Return `text` with recognised credential values replaced.
 *
 * Idempotent: already-redacted output is unchanged, so applying it twice is safe —
 * which matters because it is applied at more than one boundary.
 */
export function redactText(text: string): string {
  if (text === "") {
    return text;
  }

  // The named groups are also numbered, so the callback receives the positional
  // captures first and the groups object last. Reading the groups object by name
  // keeps this correct if the pattern ever gains a capture.
  let redacted = text.replace(ASSIGNMENT, (...args: unknown[]): string => {
    const groups = args[args.length - 1] as Record<string, string | undefined>;
    const name = groups.name ?? "";
    const value = groups.value ?? "";
    const whole = args[0] as string;

    if (value === REDACTED) {
      return whole;
    }
    if (!looksLikeSecretName(name)) {
      return whole;
    }
    if (value.length < MIN_SECRET_VALUE_LEN) {
      return whole;
    }
    const quote = groups.nq ?? "";
    return `${quote}${name}${quote}${groups.sep ?? ""} ${REDACTED}`;
  });

  for (const pattern of SECRET_VALUE_PATTERNS) {
    // Each pattern is reused across calls, so `lastIndex` must be reset.
    pattern.lastIndex = 0;
    redacted = redacted.replace(pattern, REDACTED);
  }
  return redacted;
}

/**
 * Redact a value on its way into a debug log or an error message.
 *
 * Separate name from `redactText` so the two boundaries can diverge later if
 * logging ever needs to be stricter than model-facing text.
 */
export function redactForLog(text: string): string {
  return redactText(text);
}
