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
import { redactText } from "./redaction.js";
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
