"""Composition-level auth facade for the service-period member grid."""

from aicrm_next.admin_auth.guards import admin_auth_enforcement_enabled, current_auth_context

__all__ = ["admin_auth_enforcement_enabled", "current_auth_context"]
