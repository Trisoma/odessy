"""Opt-in collaboration between named, independently configured participants."""
from odessy.collaboration.config import (
    CollaborationConfig, CollaborationSettings, ParticipantProfile, load_config, save_config,
)
from odessy.collaboration.participants import CollaborationService

__all__ = ['CollaborationConfig', 'CollaborationSettings', 'ParticipantProfile',
           'CollaborationService', 'load_config', 'save_config']
