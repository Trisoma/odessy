"""Opt-in collaboration between named, independently configured participants."""
from odessy.collaboration.config import (
    CollaborationConfig, CollaborationSettings, ParticipantProfile, load_config, save_config,
)
from odessy.collaboration.participants import CollaborationService
from odessy.collaboration.roles import (
    bindings_for, declared_roles, resolve_role, tagged_for,
)
from odessy.collaboration.planner import (
    WorkPlan, PlanStep, infer_flow, plan_from_intent, to_run_arguments, validate_plan,
)

__all__ = ['CollaborationConfig', 'CollaborationSettings', 'ParticipantProfile',
           'CollaborationService', 'load_config', 'save_config',
           'declared_roles', 'bindings_for', 'tagged_for', 'resolve_role',
           'WorkPlan', 'PlanStep', 'infer_flow', 'plan_from_intent',
           'to_run_arguments', 'validate_plan']
