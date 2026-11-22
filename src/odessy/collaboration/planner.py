"""Layer 2: turn a user's intent into a structured, untrusted work plan.

A plan is a *proposal*, not an authorization. This module never grants authority,
never starts a run, and never talks to a model. It deterministically parses an
intent, resolves each step's role to candidate participants via
``collaboration.roles``, and produces data that a caller may bind to the existing
``agent.run`` approval chain (see ``to_run_arguments``). Every step still passes
the ordinary policy, capability, allowed-mode and high-risk approval gates.

"The model proposes; Odessy decides." The planner proposes; the person decides.
"""
from __future__ import annotations

from dataclasses import dataclass

from odessy.collaboration.config import CollaborationConfig
from odessy.collaboration.roles import resolve_role
from odessy.collaboration.runs import FLOWS

#: role names used by the built-in two-step flows
DOCUMENT_ROLE = 'requirements'
CODE_ROLE = 'implementation'
REVIEW_ROLE = 'review'

#: flow -> (first step role, second step role)
FLOW_ROLES = {
    'document_to_code': (DOCUMENT_ROLE, CODE_ROLE),
    'code_to_review': (CODE_ROLE, REVIEW_ROLE),
}

# Cues are matched as whole substrings, longest first, so "design document" wins
# over "document" and an explicit participant name is preferred over a role guess.
_DOCUMENT_CUES = ('design document', 'design doc', 'requirements document', 'spec', 'document', '文档', '需求')
_CODE_CUES = ('implement', 'write code', '写完代码', '写代码', '实现', 'code')
_REVIEW_CUES = ('review', '审查', 'check', 'look over')


@dataclass(frozen=True, slots=True)
class PlanStep:
    role: str
    task: str
    participant: str = ''
    mode: str = ''

    def public_dict(self):
        return {'role': self.role, 'task': self.task,
                'participant': self.participant, 'mode': self.mode}


@dataclass(frozen=True, slots=True)
class WorkPlan:
    flow: str
    steps: tuple[PlanStep, ...]
    workspace: str = ''
    intent: str = ''

    def public_dict(self):
        return {'flow': self.flow, 'workspace': self.workspace, 'intent': self.intent,
                'steps': [step.public_dict() for step in self.steps]}


def infer_flow(text: str) -> str:
    """Return the two-step flow implied by ``text``, or '' when unclear.

    Review language wins over code language ("write code then review" is a
    code_to_review flow). Otherwise a document cue plus a code cue is a
    document_to_code flow. A single cue alone is not enough to form a flow.
    """
    if not isinstance(text, str) or not text.strip():
        return ''
    lowered = text.lower()
    wants_review = any(cue in lowered for cue in _REVIEW_CUES)
    wants_code = any(cue in lowered for cue in _CODE_CUES)
    wants_document = any(cue in lowered for cue in _DOCUMENT_CUES)
    if wants_review and (wants_code or wants_document):
        return 'code_to_review'
    if wants_document and wants_code:
        return 'document_to_code'
    return ''


#: cues that belong to each role, used to bind a mentioned name to the right step
_ROLE_CUES = {
    DOCUMENT_ROLE: _DOCUMENT_CUES,
    CODE_ROLE: _CODE_CUES,
    REVIEW_ROLE: _REVIEW_CUES,
}


def _mentioned_in_order(config: CollaborationConfig, text: str) -> list[tuple[int, str]]:
    """Configured participant names found in ``text``, ordered by position."""
    lowered = text.lower() if isinstance(text, str) else ''
    found = []
    for name in config.participants:
        idx = lowered.find(name.lower())
        if idx >= 0:
            found.append((idx, name))
    return sorted(found)


def _pick_participants_for_roles(config: CollaborationConfig, text: str,
                                 roles: tuple[str, str]) -> tuple[str, str]:
    """Bind mentioned names to the two roles by reading order.

    A name that appears before a role's cue belongs to that role
    ("claude writes the document, then codex implements"). Names are consumed
    left to right; any role left without a nearby name falls back to
    ``resolve_role``. This avoids one trailing name stealing an earlier step.
    """
    lowered = text.lower() if isinstance(text, str) else ''
    mentions = _mentioned_in_order(config, text)
    result = ['', '']
    used = set()

    def cue_position(role):
        positions = [lowered.find(cue) for cue in _ROLE_CUES.get(role, ()) if cue in lowered]
        return min(positions) if positions else None

    # Assign each mention to the earliest not-yet-assigned role whose cue comes
    # at or after the mention (the name introduces the work described next).
    for _, name in mentions:
        for index, role in enumerate(roles):
            if index in used:
                continue
            cue = cue_position(role)
            if cue is None or cue >= lowered.find(name.lower()):
                result[index] = name
                used.add(index)
                break

    # Fallback for any role still empty: first role match, preferring a name not
    # already used by the other step.
    for index, role in enumerate(roles):
        if result[index]:
            continue
        for candidate in resolve_role(config, role):
            if candidate not in result:
                result[index] = candidate
                break
        else:
            candidates = resolve_role(config, role)
            result[index] = candidates[0] if candidates else ''
    return result[0], result[1]


def _task_for(role: str, text: str) -> str:
    """A bounded, human-readable task description for a step (untrusted text)."""
    base = {
        DOCUMENT_ROLE: 'write the requirements document',
        CODE_ROLE: 'implement the code from the document',
        REVIEW_ROLE: 'review the changes',
    }.get(role, 'complete the assigned step')
    snippet = text.strip()[:280]
    return f'{base}. User intent: {snippet}' if snippet else base


def plan_from_intent(config: CollaborationConfig, text: str, workspace: str = '') -> WorkPlan | None:
    """Build a two-step :class:`WorkPlan` from ``text`` using configured roles.

    Returns ``None`` when the intent does not describe a handoff flow or when a
    step's role cannot be resolved to a configured participant. The returned
    plan is untrusted data; it grants nothing and executes nothing.
    """
    flow = infer_flow(text)
    if flow not in FLOW_ROLES:
        return None
    first_mode, second_mode = FLOWS[flow]
    first_role, second_role = FLOW_ROLES[flow]
    first, second = _pick_participants_for_roles(config, text, (first_role, second_role))
    if not first or not second:
        return None
    steps = (
        PlanStep(role=first_role, task=_task_for(first_role, text),
                 participant=first, mode=first_mode),
        PlanStep(role=second_role, task=_task_for(second_role, text),
                 participant=second, mode=second_mode),
    )
    return WorkPlan(flow=flow, steps=steps, workspace=workspace, intent=text[:2000])


def validate_plan(plan: WorkPlan, config: CollaborationConfig) -> tuple[bool, str]:
    """Check a plan is internally consistent with configuration.

    This is a configuration sanity check, NOT an authorization decision: passing
    means the plan is well-formed, not that it may run. Capabilities, allowed
    modes, rules and approval are still evaluated by the ordinary call path.
    """
    if plan.flow not in FLOWS:
        return False, 'unknown flow'
    if len(plan.steps) != 2:
        return False, 'a flow needs exactly two steps'
    expected_modes = FLOWS[plan.flow]
    for index, (step, mode) in enumerate(zip(plan.steps, expected_modes), start=1):
        profile = config.participants.get(step.participant)
        if profile is None:
            return False, f'step {index} participant is not configured'
        if not step.task.strip():
            return False, f'step {index} has no task'
        if step.mode != mode:
            return False, f'step {index} mode does not match flow'
        if mode not in profile.allowed_modes:
            return False, f'step {index} participant does not allow {mode}'
    return True, ''


def to_run_arguments(plan: WorkPlan, config: CollaborationConfig | None = None) -> dict:
    """Map a plan to ``agent.run`` arguments for approval binding.

    The caller submits these through the normal tool/approval path; this
    function does not start anything. When ``config`` is given the plan is
    checked with :func:`validate_plan` first and an invalid plan raises.
    """
    if config is not None:
        ok, reason = validate_plan(plan, config)
        if not ok:
            raise ValueError(f'invalid plan: {reason}')
    first, second = plan.steps
    arguments = {
        'flow': plan.flow,
        'from_participant': first.participant,
        'from_task': first.task,
        'to_participant': second.participant,
        'to_task': second.task,
    }
    if plan.workspace:
        arguments['workspace'] = plan.workspace
    return arguments
