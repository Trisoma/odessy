/**
 * Where the confinement runner lives — and the one thing this module must never
 * do is import it.
 *
 * `runner.ts` calls `main()` at module scope. It is a script, not a library:
 * importing it creates a restricted token and spawns a process as a side effect
 * of a module load. This module therefore exposes the runner's *location* and
 * never its contents, so a caller can spawn it without risking an accidental
 * import somewhere down a dependency chain.
 *
 * The path is derived from this module's own URL rather than `process.cwd()`, so
 * it resolves identically however the parent process was launched. It is a URL
 * build rather than an import specifier on purpose: TypeScript rewrites import
 * specifiers, but leaves `new URL(...)` alone, so the string keeps pointing at
 * the sibling `runner.js` in the compiled output.
 */

import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

/** The compiled runner's absolute path. Existence is not implied; check it. */
export const RUNNER_ENTRY = fileURLToPath(new URL('./runner.js', import.meta.url))

/** Whether the compiled runner is present beside this module. */
export function runnerEntryExists(): boolean {
  return existsSync(RUNNER_ENTRY)
}
