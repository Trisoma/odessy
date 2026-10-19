import assert from "node:assert/strict";
import test from "node:test";

import {
  ProviderContractError,
  UnsatisfiableRequirementError,
  type Capability,
  type ConfineResult,
  type Enforcement,
  type Evaluation,
  type ExecutionPolicy,
  type ExecutionProvider,
  type Limitation,
  type ProbeReport,
  type Requirement,
} from "../src/execution/contract.js";
import { UnconfinedProvider } from "../src/execution/providers/unconfined.js";
import {
  ProviderRegistry,
  renderSelection,
  withUnconfinedFallback,
} from "../src/execution/registry.js";

interface StubOptions {
  enforcement?: Enforcement;
  capabilities?: readonly Capability[];
  available?: boolean;
  satisfied?: boolean;
  limitations?: readonly Limitation[];
  reason?: string;
}

class StubProvider implements ExecutionProvider {
  readonly confineCalls: (readonly string[])[] = [];

  constructor(
    readonly providerId: string,
    private readonly options: StubOptions = {},
  ) {}

  capabilities(): readonly Capability[] {
    return (
      this.options.capabilities ?? ["process-exec", "fs-read", "fs-write", "network", "process-visibility"]
    );
  }

  probe(): ProbeReport {
    const available = this.options.available ?? true;
    return {
      providerId: this.providerId,
      available,
      facts: {},
      reason: available ? "" : "stub is unavailable",
    };
  }

  evaluate(_requirement: Requirement): Evaluation {
    return {
      satisfied: this.options.satisfied ?? true,
      enforcement: this.options.enforcement ?? "full",
      limitations: this.options.limitations ?? [],
      reason: this.options.reason ?? "",
    };
  }

  confine(argv: readonly string[], _policy: ExecutionPolicy): ConfineResult {
    this.confineCalls.push(argv);
    return {
      argv,
      enforcement: this.options.enforcement ?? "full",
      limitations: this.options.limitations ?? [],
      denialSignatures: [],
      runnerFailureRules: [],
      providerId: this.providerId,
    };
  }
}

function requirement(
  options: {
    mode?: ExecutionPolicy["mode"];
    isolation?: Requirement["isolation"];
    capabilities?: Requirement["capabilities"];
  } = {},
): Requirement {
  return {
    policy: { mode: options.mode ?? "workspace-write", workspaceRoot: "/tmp", deviceGrants: [] },
    isolation: options.isolation ?? "preferred",
    capabilities: options.capabilities ?? ["process-exec"],
  };
}

// --------------------------------------------------------------------------
// Selection among satisfying providers
// --------------------------------------------------------------------------

test("the satisfying provider is selected", () => {
  const provider = new StubProvider("only");
  const selection = new ProviderRegistry([provider]).select(requirement());

  assert.equal(selection.provider, provider);
  assert.equal(selection.evaluation.enforcement, "full");
});

test("the strongest enforcement wins", () => {
  // Ranking only chooses among providers that already satisfy the request.
  const partial = new StubProvider("partial", {
    enforcement: "partial",
    limitations: ["cannot_deny_truncate"],
  });
  const full = new StubProvider("full");

  const selection = new ProviderRegistry([partial, full, new UnconfinedProvider()]).select(
    requirement(),
  );

  assert.equal(selection.provider.providerId, "full");
});

test("ties resolve to the earliest registration", () => {
  const first = new StubProvider("first");
  const second = new StubProvider("second");

  assert.equal(new ProviderRegistry([first, second]).select(requirement()).provider.providerId, "first");
  assert.equal(new ProviderRegistry([second, first]).select(requirement()).provider.providerId, "second");
});

// --------------------------------------------------------------------------
// Pre-filtering and unavailability
// --------------------------------------------------------------------------

test("a provider without the needed capability is filtered out", () => {
  const narrow = new StubProvider("narrow", { capabilities: ["process-exec"] });
  const wide = new StubProvider("wide", { capabilities: ["process-exec", "network"] });

  const selection = new ProviderRegistry([narrow, wide]).select(
    requirement({ capabilities: ["process-exec", "network"] }),
  );

  assert.equal(selection.provider.providerId, "wide");
  const reasons = new Map(selection.considered);
  assert.match(reasons.get("narrow")?.reason ?? "", /does not offer capabilities/);
});

test("an unavailable provider is recorded but not selected", () => {
  const broken = new StubProvider("broken", { available: false });
  const working = new StubProvider("working");

  const selection = new ProviderRegistry([broken, working]).select(requirement());

  assert.equal(selection.provider.providerId, "working");
  assert.deepEqual(
    selection.unavailable.map((report) => report.providerId),
    ["broken"],
  );
});

test("an unavailable provider never satisfies, even if it claims to", () => {
  // Availability is decided before evaluation.
  const liar = new StubProvider("liar", { available: false, satisfied: true });

  assert.throws(() => new ProviderRegistry([liar]).select(requirement()), UnsatisfiableRequirementError);
});

// --------------------------------------------------------------------------
// The negative test: required means required
// --------------------------------------------------------------------------

test("required isolation denies when nothing satisfies it", () => {
  for (const provider of [
    new StubProvider("broken", { available: false }),
    new StubProvider("unsatisfied", { satisfied: false }),
  ]) {
    assert.throws(
      () => new ProviderRegistry([provider]).select(requirement({ isolation: "required" })),
      UnsatisfiableRequirementError,
      provider.providerId,
    );
    assert.deepEqual(provider.confineCalls, [], "nothing may run after a denied selection");
  }
});

test("required isolation refuses the unconfined fallback", () => {
  const unconfined = new UnconfinedProvider();

  assert.throws(
    () => new ProviderRegistry([unconfined]).select(requirement({ isolation: "required" })),
    /isolation was required/,
  );
  assert.equal(unconfined.evaluate(requirement({ isolation: "required" })).satisfied, false);
});

test("required isolation denies before any weaker provider runs", () => {
  const weak = new StubProvider("weak", {
    enforcement: "partial",
    limitations: ["cannot_deny_truncate"],
    satisfied: false,
    reason: "old ABI cannot deny truncate",
  });
  const registry = withUnconfinedFallback([weak]);

  assert.throws(
    () => registry.select(requirement({ isolation: "required" })),
    /old ABI cannot deny truncate/,
  );
  assert.deepEqual(weak.confineCalls, []);
  assert.equal(registry.providers().at(-1)?.providerId, "unconfined");
});

test("a required request is satisfied when a backend can meet it", () => {
  const strong = new StubProvider("strong");
  const selection = new ProviderRegistry([strong, new UnconfinedProvider()]).select(
    requirement({ isolation: "required" }),
  );

  assert.equal(selection.provider.providerId, "strong");
});

// --------------------------------------------------------------------------
// The unconfined fallback
// --------------------------------------------------------------------------

test("the unconfined fallback is used when isolation is not required", () => {
  const selection = new ProviderRegistry([new UnconfinedProvider()]).select(requirement());

  assert.equal(selection.provider.providerId, "unconfined");
  assert.equal(selection.evaluation.enforcement, "none");
});

test("the unconfined fallback reports none rather than claiming a boundary", () => {
  const result = new UnconfinedProvider().confine(["true"], {
    mode: "workspace-write",
    workspaceRoot: "/tmp",
    deviceGrants: [],
  });

  assert.deepEqual(result.argv, ["true"]);
  assert.equal(result.enforcement, "none");
  assert.deepEqual(result.denialSignatures, []);
  assert.deepEqual(result.runnerFailureRules, []);
});

test("the unconfined fallback never beats a real backend", () => {
  const real = new StubProvider("real", {
    enforcement: "partial",
    limitations: ["cannot_deny_truncate"],
  });

  const selection = new ProviderRegistry([new UnconfinedProvider(), real]).select(requirement());

  assert.equal(selection.provider.providerId, "real");
});

test("the fallback is registered last", () => {
  const registry = withUnconfinedFallback([new StubProvider("real")]);

  assert.deepEqual(
    registry.providers().map((provider) => provider.providerId),
    ["real", "unconfined"],
  );
});

test("an empty registry denies", () => {
  assert.throws(() => new ProviderRegistry().select(requirement()), /no providers registered/);
});

// --------------------------------------------------------------------------
// Contract enforcement during selection
// --------------------------------------------------------------------------

test("a partial provider must name its limitation", () => {
  const defective = new StubProvider("defective", { enforcement: "partial", limitations: [] });

  assert.throws(
    () => new ProviderRegistry([defective]).select(requirement()),
    ProviderContractError,
  );
});

// --------------------------------------------------------------------------
// Reporting
// --------------------------------------------------------------------------

test("the rendered selection names the choice and the candidates", () => {
  const satisfied = new StubProvider("good", { reason: "works" });
  const rejected = new StubProvider("bad", { satisfied: false, reason: "no backend" });

  const rendered = renderSelection(
    new ProviderRegistry([satisfied, rejected]).select(requirement()),
  );

  assert.match(rendered, /selected good/);
  assert.match(rendered, /enforcement=full/);
  assert.match(rendered, /no backend/);
});

test("the rendered selection lists limitations", () => {
  const limited = new StubProvider("limited", {
    enforcement: "partial",
    limitations: ["cannot_deny_truncate"],
  });

  const rendered = renderSelection(new ProviderRegistry([limited]).select(requirement()));

  assert.match(rendered, /cannot_deny_truncate/);
});
