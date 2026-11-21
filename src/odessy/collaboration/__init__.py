"""Opt-in collaboration between named, independently configured participants."""
from odessy.collaboration.config import (
    CollaborationConfig, CollaborationSettings, ParticipantProfile, load_config, save_config,
)
from odessy.collaboration.participants import CollaborationService
from odessy.collaboration.roles import (
    bindings_for, declared_roles, resolve_role, tagged_for,
)

__all__ = ['CollaborationConfig', 'CollaborationSettings', 'ParticipantProfile',
           'CollaborationService', 'load_config', 'save_config',
           'declared_roles', 'bindings_for', 'tagged_for', 'resolve_role']
