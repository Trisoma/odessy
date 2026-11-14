"""Bounded context, explicit memory and lexical retrieval (Wave 2, prompt 04)."""
from .composition import Wave2Services, build_wave2_services
from .schema import EXTENSION_VERSION, migrate_extensions
from .service import ContextService

__all__ = ['ContextService', 'Wave2Services', 'build_wave2_services',
           'migrate_extensions', 'EXTENSION_VERSION']
