/**
 * Pure text retention: bounded head and tail windows over UTF-8 text.
 *
 * TypeScript mirror of `odessy/output_retention.py`, which is itself ported from
 * the upstream `output-retention` package. It is a library rather than a
 * config-loaded plugin precisely because retention is *pure*: it caps how much
 * text is shown and reports exactly what it withheld, and nothing else.
 *
 * Deliberately carries no I/O. Persisting withheld content is a separate concern
 * (see `spill.ts`), which is what keeps this module trivially testable and
 * prevents a retention bug from ever destroying data.
 *
 * Two semantics are preserved exactly, because both are easy to get subtly wrong:
 *
 * 1. Budgets and `omitted_bytes` count **bytes**, not characters or lines. A cap
 *    expressed in characters would be wrong for multi-byte output.
 *
 * 2. The omitted count is measured **after** UTF-8 boundary trimming, never
 *    derived from the budget. Trimming can drop up to three extra bytes per cut,
 *    so a notice computed from the budget would overstate what was kept and thus
 *    be a false statement about the data.
 */

/**
 * Longest UTF-8 sequence (4 bytes) minus one.
 *
 * Trimming can never need to drop more than this many bytes to reach a codepoint
 * boundary.
 */
const MAX_UTF8_LEN = 4;

/** Throws on invalid UTF-8. This is the `UnicodeDecodeError` analogue. */
const STRICT = new TextDecoder("utf-8", { fatal: true });

/** Whether `data` decodes as valid, complete UTF-8. */
function isValidUtf8(data: Uint8Array): boolean {
  try {
    STRICT.decode(data);
    return true;
  } catch {
    return false;
  }
}

/**
 * How much content the retainer withheld.
 *
 * `exact` means the count is precise, which byte-oriented retention always can
 * be. `unknown` is reserved for a caller that omits content without counting it;
 * it exists so downstream code never has to guess whether a number is trustworthy.
 */
export interface Omitted {
  readonly kind: "exact" | "unknown";
  readonly count: number;
}

function exact(count: number): Omitted {
  return { kind: "exact", count };
}

/**
 * Longest prefix of `data` that ends on a UTF-8 codepoint boundary.
 *
 * `data` is assumed to be a prefix of valid UTF-8, so only the final bytes can be
 * cut. Candidates are tried longest-first, which is why the first success returns
 * immediately.
 */
function trimPrefixToBoundary(data: Uint8Array): Uint8Array {
  for (let drop = 0; drop <= Math.min(MAX_UTF8_LEN, data.length); drop++) {
    const candidate = data.subarray(0, data.length - drop);
    if (isValidUtf8(candidate)) {
      return candidate;
    }
  }
  return data.subarray(0, 0);
}

/**
 * Longest suffix of `data` that starts on a UTF-8 codepoint boundary.
 *
 * The *end* of `data` is already a boundary (it is the end of the original text),
 * so only leading bytes — a partial sequence — need dropping.
 */
function trimSuffixToBoundary(data: Uint8Array): Uint8Array {
  for (let drop = 0; drop < Math.min(MAX_UTF8_LEN, data.length); drop++) {
    const candidate = data.subarray(drop);
    if (isValidUtf8(candidate)) {
      return candidate;
    }
  }
  return data.subarray(0, 0);
}

/** The inline text plus the mechanical facts a notice needs. */
export interface RetentionResult {
  readonly text: string;
  readonly kept_prefix_bytes: number;
  readonly kept_suffix_bytes: number;
  readonly omitted_bytes: Omitted;
  readonly truncated: boolean;
  readonly total_bytes: number;
}

/**
 * Head-and-tail byte budget over UTF-8 text.
 *
 * `headBytes` and `tailBytes` are the inline budget. Text at or under the
 * combined budget is returned unchanged and uncut; larger text keeps both ends and
 * reports the exact omitted byte count.
 */
export interface TextRetainerOptions {
  readonly headBytes: number;
  readonly tailBytes: number;
}

export class TextRetainer {
  readonly headBytes: number;
  readonly tailBytes: number;

  constructor(options: TextRetainerOptions) {
    this.headBytes = Math.max(0, Math.trunc(options.headBytes));
    this.tailBytes = Math.max(0, Math.trunc(options.tailBytes));
  }

  get budgetBytes(): number {
    return this.headBytes + this.tailBytes;
  }

  /** Return `text` bounded to the budget, with exact omission metadata. */
  retain(text: string): RetentionResult {
    const data = Buffer.from(text, "utf8");
    const total = data.length;

    if (total <= this.budgetBytes) {
      // Nothing withheld, so prefix and suffix are adjacent slices of one buffer
      // and no boundary trim is warranted or needed.
      return {
        text,
        kept_prefix_bytes: total,
        kept_suffix_bytes: 0,
        omitted_bytes: exact(0),
        truncated: false,
        total_bytes: total,
      };
    }

    const prefix = trimPrefixToBoundary(data.subarray(0, this.headBytes));
    const suffix = trimSuffixToBoundary(data.subarray(data.length - this.tailBytes));

    const keptPrefix = prefix.length;
    const keptSuffix = suffix.length;
    // Measured, not derived: the budget is not what was kept.
    const omitted = total - keptPrefix - keptSuffix;

    if (omitted <= 0) {
      // A pathological budget (a tail larger than the text, say) can leave the two
      // windows overlapping. Adjacent, complete text beats a negative omission
      // count and a fabricated notice.
      return {
        text,
        kept_prefix_bytes: total,
        kept_suffix_bytes: 0,
        omitted_bytes: exact(0),
        truncated: false,
        total_bytes: total,
      };
    }

    const parts = [Buffer.from(prefix).toString("utf8")];
    if (keptSuffix > 0) {
      parts.push(Buffer.from(suffix).toString("utf8"));
    }

    return {
      text: parts.join(""),
      kept_prefix_bytes: keptPrefix,
      kept_suffix_bytes: keptSuffix,
      omitted_bytes: exact(omitted),
      truncated: true,
      total_bytes: total,
    };
  }
}

/**
 * Render a consistent omission clause, optionally with recovery guidance.
 *
 * The split mirrors upstream: this module owns the *mechanical* clause — what was
 * withheld and how much — while the caller owns recovery guidance, since only the
 * caller knows where the full content went. A notice without a recovery hint is
 * still honest, just less useful.
 */
export function formatRetentionNotice(
  result: RetentionResult,
  options: { unitLabel?: string; recovery?: string | null } = {},
): string {
  if (!result.truncated) {
    return "";
  }
  const unitLabel = options.unitLabel ?? "output";
  let notice =
    `[odessy: ${unitLabel} truncated -- omitted ` +
    `${result.omitted_bytes.count} bytes of ${result.total_bytes}]`;
  if (options.recovery) {
    notice = `${notice} ${options.recovery}`;
  }
  return notice;
}
