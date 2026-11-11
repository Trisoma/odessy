"""Odessy model profiles -> a Harness provider route, without a second provider registry.

What the pinned runtime offers by itself (measured, prompt 04): one provider group,
``deepseek-official``, with three model ids. Odessy supports more routes than that, so the
mapping uses the maintained multi-provider adapter the pin already mounts —
``@deepseek-ai/dsh-llm-pi-ai`` — and declares **one** route for the selected profile:
``api`` + ``baseURL`` + ``models``. Measured at the pin: a hand-declared route appears in
``configOptions.model``, is selectable with ``session/set_config_option``, and a turn
reaches the declared endpoint (``/v1/chat/completions`` for ``openai-completions``,
``/v1/messages`` for ``anthropic-messages``).

Reuse instead of reimplementation:

- the credential comes from :func:`odessy.credentials.resolve`, i.e. Odessy's existing
  precedence (session > user override > environment > legacy vault entry);
- endpoint validation, model/default resolution, ``ODESSY_CLOUD_*`` overrides, output
  ceilings and reasoning effort come from :func:`odessy.models.cloud_model.build_cloud_model`
  — the same constructor the legacy engine uses. This module reads its fields and discards
  the adapter; it does not re-implement any of that logic.

Secrets: exactly one credential value ever enters the child, under one variable name
(:data:`KEY_ENV`), and only for the route the session is launched with. The child's
environment is an allowlist, so an inherited ``ANTHROPIC_API_KEY`` cannot override a saved
or session value — that value is only ever read in-process by
:func:`odessy.credentials.resolve`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from odessy.runtime_backends.contracts import ModelRouteError

#: The single variable the child receives a credential in. A fixed name (rather than the
#: provider's ambient variable) keeps the child from seeing any other saved key.
KEY_ENV = "ODESSY_HARNESS_PROVIDER_KEY"

#: Route names are prefixed so they cannot collide with a route a mounted adapter owns
#: (``deepseek-official`` belongs to the DeepSeek adapter; pi-ai may own ``openai``).
ROUTE_PREFIX = "odessy-"

#: Prefix of the route overlay files materialised inside the owned Harness home. Like the
#: narrow overlay they are generated, not shipped: the application source tree admits only
#: reviewed file types (``installer/components/application.py``).
#:
#: The file name carries a digest of its own content, so it is **content-addressed**:
#: two sessions that declare different endpoints cannot overwrite each other, and an
#: existing file with the same name is by construction the same bytes.
ROUTE_PATCH_PREFIX = "odessy_route-"

#: pi-ai wire apis, keyed by the protocol Odessy records for the provider.
WIRE_API = {"openai": "openai-completions", "anthropic": "anthropic-messages"}

#: Thinking levels pi-ai accepts per route (``catalog.ts`` at the snapshot; a level outside
#: this set is refused instead of silently ignored).
THINKING_LEVELS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh", "max"})

#: Adapter -> (status, note). ``supported`` means "expressed and measured at the pin";
#: anything else is refused visibly rather than routed somewhere else.
ROUTE_SUPPORT: dict[str, tuple[str, str]] = {
    "cloud": ("supported", "declared pi-ai route; both wire apis measured"),
    "ollama": ("unverified-local",
               "Odessy talks Ollama's native /api/chat; a Harness route would need that "
               "server's OpenAI-compatible /v1 surface, which no local server was "
               "available to verify in this task"),
    "lfm": ("unverified-local",
            "same as ollama: native /api/chat on the Odessy side, unverified /v1 surface"),
    "auto": ("unsupported",
             "Odessy's routing/failover heuristics are legacy-engine behaviour; the Harness "
             "engine refuses them instead of choosing a provider for the user"),
    "codex": ("unsupported", "consultation adapter, not a model route"),
    "claude_code": ("unsupported", "consultation adapter, not a model route"),
    "custom_agent": ("unsupported", "consultation adapter, not a model route"),
}


@dataclass(frozen=True)
class HarnessRoute:
    """One selected profile, expressed as the route the runtime is launched with."""

    adapter: str
    provider: str
    model: str
    api: str
    base_url: str
    max_tokens: int
    reasoning: str | None = None
    api_key: str = field(default="", repr=False)
    profile_name: str = ""
    #: Other models the session may switch to on this same route (other profiles that use
    #: the same provider). Declared up front so a model change needs no relaunch.
    extra_models: tuple[str, ...] = ()

    @property
    def route(self) -> str:
        """Return the stable route identity used in the runtime profile."""
        return ROUTE_PREFIX + self.provider

    def option_value(self) -> str:
        """The wire value of one ``model`` option: JSON ``[provider, model]`` (contract §4)."""
        return json.dumps([self.route, self.model], separators=(",", ":"))

    def environment(self) -> dict[str, str]:
        """Return only the declared credential variable for this route."""
        return {KEY_ENV: self.api_key}

    def overlay_text(self) -> str:
        """The single-row patch that declares this route to ``llm-pi-ai``.

        JSON is valid YAML 1.2, so the nested ``config`` is emitted as a flow mapping and
        this file needs no YAML dependency. ``no secret enters the configuration file``:
        the credential is referenced by name only.
        """
        profile: dict[str, Any] = {
            "displayName": f"Odessy {self.provider}",
            "api": self.api,
            "baseURL": self.base_url,
            "apiKeyEnv": KEY_ENV,
            "models": [{"id": name, "maxTokens": self.max_tokens}
                       for name in (self.model, *self.extra_models)],
        }
        if self.reasoning:
            profile["reasoning"] = self.reasoning
        config = {"providers": {self.route: profile}}
        return ("# Generated by odessy.runtime_backends.harness.routes; do not edit.\n"
                "- id: llm-pi-ai\n"
                f"  config: {json.dumps(config, ensure_ascii=False, sort_keys=True)}\n")

    def public(self) -> dict[str, Any]:
        """Safe to display or log: identity and limits, never the value."""
        return {"adapter": self.adapter, "provider": self.provider, "route": self.route,
                "model": self.model, "api": self.api, "base_url": self.base_url,
                "max_tokens": self.max_tokens, "reasoning": self.reasoning,
                "models": [self.model, *self.extra_models],
                "credential": KEY_ENV if self.api_key else ""}


def _profile_value(profile: Mapping[str, Any], name: str) -> str:
    """Read a normalized optional string from a model profile."""
    value = profile.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _unsupported(adapter: str, profile_name: str) -> "ModelRouteError":
    """Build a named route error instead of silently selecting another adapter."""
    status, note = ROUTE_SUPPORT.get(adapter, ("unsupported", "unknown adapter"))
    where = f"Model profile {profile_name!r} " if profile_name else ""
    return ModelRouteError(
        f"{where}(adapter {adapter!r}) cannot run on the Harness engine "
        f"({status}): {note}"
    )


def cloud_environment(profile: Mapping[str, Any], environ: Mapping[str, str]) -> dict[str, str]:
    """The profile's endpoint/model on top of the environment, in the variables the
    existing cloud constructor reads. Absent fields keep the caller's value."""
    env = dict(environ)
    model = _profile_value(profile, "model")
    endpoint = _profile_value(profile, "endpoint")
    if model:
        env["ODESSY_CLOUD_MODEL"] = model
    if endpoint:
        env["ODESSY_CLOUD_BASE_URL"] = endpoint
    ceiling = profile.get("max_tokens")
    if ceiling is not None:
        if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling <= 0:
            raise ModelRouteError("max_tokens must be a positive integer")
        env["ODESSY_CLOUD_MAX_TOKENS"] = str(ceiling)
        env["ODESSY_CLOUD_OPENAI_MAX_TOKENS"] = str(ceiling)
    return env


def resolve_route(
    profile: Mapping[str, Any] | None,
    *,
    environ: Mapping[str, str] | None = None,
    profile_name: str = "",
    require_credential: bool = True,
    extra_models: "Sequence[str]" = (),
) -> HarnessRoute:
    """Resolve one Odessy profile to a Harness route, or refuse with a named reason.

    Raises :class:`ModelRouteError` for a route the Harness cannot express and
    :class:`~odessy.runtime_backends.contracts.RuntimeUnavailable` when a supported route
    has no credential — never a silent substitution.
    """
    import os

    from odessy.models.cloud_model import CLOUD_PROVIDERS, CloudModelConfigError, build_cloud_model
    from odessy.runtime_backends.contracts import RuntimeUnavailable

    environ = os.environ if environ is None else environ
    profile = dict(profile or {})
    name = profile_name or _profile_value(profile, "name")
    adapter = _profile_value(profile, "adapter")
    if not adapter:
        # The CLI's ``current`` profile carries only a model name; the backend it was built
        # from is in the environment, exactly as ``cli._build_model`` reads it.
        adapter = (environ.get("ODESSY_MODEL_BACKEND") or "lfm").strip().lower()
        if adapter in CLOUD_PROVIDERS:
            # A cloud backend name *is* a provider: the adapter is ``cloud``, and the
            # backend name becomes the provider. (Conflating the two refused every
            # ``ODESSY_MODEL_BACKEND=deepseek`` session as an unknown adapter.)
            profile.setdefault("provider", adapter)
            adapter = "cloud"
        else:
            adapter = "lfm" if adapter == "llama_cpp" else adapter
            adapter = adapter if adapter in ROUTE_SUPPORT else "lfm"
    if adapter != "cloud":
        raise _unsupported(adapter, name)

    provider = _profile_value(profile, "provider") or "openai"
    preset = CLOUD_PROVIDERS.get(provider)
    if preset is None:
        raise ModelRouteError(
            f"Unknown cloud provider {provider!r} in model profile {name!r}; expected one of: "
            + ", ".join(sorted(CLOUD_PROVIDERS))
        )
    env = cloud_environment(profile, environ)
    reference = _profile_value(profile, "credential_env")
    if not require_credential:
        # Identity-only resolution, so "this is another provider" can be answered without
        # the other provider being configured. The value is never used for a request.
        env.setdefault(reference or "ODESSY_CLOUD_API_KEY", "identity-only-placeholder")
        if reference:
            env.setdefault(reference, "identity-only-placeholder")
    try:
        # Validation, defaults, endpoint checks, ODESSY_CLOUD_* overrides and credential
        # resolution all belong to the constructor the legacy engine already uses.
        model = build_cloud_model(provider, env, credential_ref=reference)
    except CloudModelConfigError as exc:
        raise RuntimeUnavailable("harness", f"model route {provider!r} is not usable",
                                 detail=str(exc)) from exc

    anthropic = preset.protocol == "anthropic"
    effort = (model.effort or "").strip().lower() or None
    if effort is not None and effort not in THINKING_LEVELS:
        raise ModelRouteError(
            f"Reasoning effort {effort!r} in model profile {name!r} is not a level this "
            "runtime accepts: " + ", ".join(sorted(THINKING_LEVELS))
        )
    return HarnessRoute(
        adapter=adapter,
        provider=provider,
        model=model.model,
        api=WIRE_API[preset.protocol],
        base_url=model.base_url,
        max_tokens=model.max_tokens if anthropic else model.openai_max_tokens,
        reasoning=effort,
        api_key=model.api_key,
        profile_name=name,
        extra_models=tuple(model_id for model_id in dict.fromkeys(extra_models)
                           if model_id and model_id != model.model),
    )


def route_patch_name(route: "HarnessRoute") -> str:
    """The content-addressed file name for this route's overlay."""
    import hashlib

    digest = hashlib.sha256(route.overlay_text().encode("utf-8", "replace")).hexdigest()
    return f"{ROUTE_PATCH_PREFIX}{digest[:16]}.patch.yml"


def materialize_route_patch(home, route: "HarnessRoute") -> "Any":
    """Write this route's overlay into the owned Harness home and return its path.

    The name is a digest of the content, so distinct routes never share a file and a
    same-named file already holds these exact bytes. The write itself uses a unique
    scratch name and an atomic replace, so two sessions (or two threads) materialising at
    the same moment cannot interleave.
    """
    import os
    import uuid
    from pathlib import Path

    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    target = home / route_patch_name(route)
    text = route.overlay_text()
    try:
        if target.read_text(encoding="utf-8") == text:
            return target
    except OSError:
        pass
    scratch = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.writing")
    try:
        scratch.write_text(text, encoding="utf-8", newline="\n")
        os.replace(scratch, target)
    finally:
        try:
            scratch.unlink()
        except OSError:
            pass
    return target


def compatibility() -> list[dict[str, str]]:
    """The route table, for the API/docs: every adapter and whether it is expressible."""
    return [{"adapter": adapter, "status": status, "note": note}
            for adapter, (status, note) in sorted(ROUTE_SUPPORT.items())]
