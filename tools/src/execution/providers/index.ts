/**
 * Execution providers.
 *
 * Each provider answers one question: *can this host confine under this request,
 * and how completely?* Providers are selected by capability, never by platform
 * name. See `registry.ts`.
 */

import { BwrapProvider } from "./bwrap.js";
import type { ConfiningProvider } from "./base.js";
import { LandlockProvider } from "./landlock.js";
import { SeatbeltProvider } from "./seatbelt.js";

export { ConfiningProvider } from "./base.js";
export { BwrapProvider } from "./bwrap.js";
export { LandlockProvider } from "./landlock.js";
export { SeatbeltProvider } from "./seatbelt.js";
export { UnconfinedProvider } from "./unconfined.js";

/**
 * The same-world backends Odessy ships, in preference order.
 *
 * The order is a tie-break only: it decides between providers that already
 * satisfy the request, and can never turn an unsatisfied request into a satisfied
 * one. bwrap leads because it restricts more by construction — a private PID
 * namespace and a fresh `/dev` — than the other two, which restrict file writes
 * and little else.
 *
 * On any given host most of these report themselves unavailable, which is the
 * intended outcome: an unavailable backend is a fact the report states, not a
 * failure.
 */
export function defaultExecutionProviders(): ConfiningProvider[] {
  return [new BwrapProvider(), new LandlockProvider(), new SeatbeltProvider()];
}
