import { Buffer } from "node:buffer";

import type { Truncation } from "./contracts.js";

export const DEFAULT_MAX_BYTES = 32 * 1024;
export const DEFAULT_MAX_RESULTS = 100;

export function truncateUtf8(
  content: string,
  maxBytes = DEFAULT_MAX_BYTES,
): { content: string; truncated: boolean; truncation?: Truncation } {
  const originalBytes = Buffer.byteLength(content, "utf8");
  if (originalBytes <= maxBytes) {
    return { content, truncated: false };
  }

  const buffer = Buffer.from(content, "utf8").subarray(0, maxBytes);
  return {
    content: buffer.toString("utf8"),
    truncated: true,
    truncation: {
      truncated: true,
      maxBytes,
      originalBytes,
    },
  };
}

export function normalizeLimit(value: unknown, defaultLimit = DEFAULT_MAX_RESULTS): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return defaultLimit;
  }
  return Math.max(1, Math.min(Math.floor(value), DEFAULT_MAX_RESULTS));
}
