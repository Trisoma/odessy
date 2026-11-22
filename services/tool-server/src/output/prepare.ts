/**
 * Prepare captured output for the model: redact, bound both ends, spill the rest.
 *
 * TypeScript mirror of `CommandBridge._prepare_output`. The order is not
 * negotiable, and it is the whole reason this is one function rather than three
 * calls at the call site:
 *
 * 1. **Redact first.** A secret must never reach the spill file, and once the file
 *    exists on disk it cannot be recalled. Redacting only the model-facing text
 *    would leave the credential persisted.
 * 2. **Retain head and tail.** The tail carries build and test failures, which a
 *    head-only cut discards precisely when it matters.
 * 3. **Spill the full text**, and if that fails keep the ORIGINAL.
 *
 * The failure branch deliberately returns untruncated content. A storage failure
 * must not become silent data loss; the cost is a large observation, and that cost
 * is the caller's to weigh, not this function's to hide.
 */

import { formatRetentionNotice, TextRetainer } from "./retention.js";
import { looksLikeSecretName, REDACTED, redactText } from "./redaction.js";
import type { SpillLocator, SpillStore } from "./spill.js";

/**
 * Fraction of the inline budget kept from the END of the output.
 *
 * Terminal errors and build failures appear at the tail, so the tail gets the
 * larger share when both ends cannot be kept together.
 */
export const TAIL_SHARE = 0.6;

export interface OutputBudget {
  readonly label: string;
  readonly maxChars: number;
  readonly spill: SpillStore;
}

export interface PreparedOutput {
  readonly text: string;
  readonly truncated: boolean;
  readonly spill: SpillLocator | null;
}

/** Redact, bound, and spill one captured stream. */
export function prepareOutput(text: string, budget: OutputBudget): PreparedOutput {
  const redacted = redactText(text);

  const tailBudget = Math.trunc(budget.maxChars * TAIL_SHARE);
  const retainer = new TextRetainer({
    headBytes: Math.max(0, budget.maxChars - tailBudget),
    tailBytes: tailBudget,
  });

  const result = retainer.retain(redacted);
  if (!result.truncated) {
    return { text: redacted, truncated: false, spill: null };
  }

  const locator = budget.spill.saveText(redacted, budget.label);
  if (locator === null) {
    // Storage failed. Preserve everything rather than truncate.
    return { text: redacted, truncated: false, spill: null };
  }

  const notice = formatRetentionNotice(result, {
    unitLabel: budget.label,
    recovery: `Full output: ${locator.relative_path}`,
  });

  return { text: `${result.text}\n${notice}\n`, truncated: true, spill: locator };
}

/**
 * Redact every string inside a structured tool result, keeping its shape.
 *
 * This is step 1 of the pipeline above, applied to the result objects the other
 * tools return (`filesystem.read`, `project.grep`, `git.*`, …). It is a separate
 * function because those results are not one captured stream: retention and
 * spilling need a budget and a field to spill, while redaction applies to every
 * string at every depth, including a third-party server's own nested payload.
 *
 * The walk rebuilds containers instead of mutating them. A tool result is also a
 * value its caller may still hold, and no redaction boundary should quietly
 * rewrite the caller's data. Ordering is unchanged for captured output: it goes
 * through `prepareOutput`, which redacts before retaining and spilling.
 *
 * A field whose **name** says it holds a credential is redacted whatever its value
 * looks like. `redactText` recognises `NAME=value` inside text and a few token
 * shapes; `{"password": "correct-horse-battery-staple"}` is neither, so a walk
 * that only inspected values would let an opaque secret through. Here the name is
 * the evidence, and it decides.
 *
 * Only JSON-shaped values are walked. MCP results are dictionaries, arrays and
 * scalars by contract; anything else is returned as-is rather than guessed at.
 */
export function redactResult<T>(value: T): T {
  if (typeof value === "string") {
    return redactText(value) as T;
  }
  if (Array.isArray(value)) {
    return value.map((item) => redactResult(item)) as T;
  }
  if (value !== null && typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      result[key] = looksLikeSecretName(key) ? redactAllStrings(item) : redactResult(item);
    }
    return result as T;
  }
  return value;
}

/** Redact strings and numbers under a credential-named field, keeping structure. */
function redactAllStrings<T>(value: T): T {
  if (typeof value === "string") {
    return (value === "" ? value : REDACTED) as T;
  }
  if (typeof value === "number") {
    return REDACTED as T;
  }
  if (Array.isArray(value)) {
    return value.map((item) => redactAllStrings(item)) as T;
  }
  if (value !== null && typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      result[key] = redactAllStrings(item);
    }
    return result as T;
  }
  return value;
}
