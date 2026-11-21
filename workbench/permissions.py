"""Minimal role capabilities for the Workbench scaffold.

This is a local scaffold, not production authentication.  Adoption and publish
are intentionally separate capabilities.
"""
from __future__ import annotations

ROLES = {
    "viewer": {"read"},
    "reviewer": {"read", "verify", "review", "map", "import"},
    "adopter": {"read", "verify", "review", "map", "import", "adopt"},
    "publisher": {"read", "publish"},
    "admin": {"read", "verify", "review", "map", "import", "adopt", "publish", "catalogue"},
}
DEFAULT_ROLE = "viewer"


def capabilities(role: str) -> set[str]:
    return ROLES.get(role, ROLES[DEFAULT_ROLE])


def can_verify(role: str) -> bool:
    return "verify" in capabilities(role)


def can_review_statement(role: str) -> bool:
    return "review" in capabilities(role)


def can_map(role: str) -> bool:
    return "map" in capabilities(role)


def can_adopt(role: str) -> bool:
    return "adopt" in capabilities(role)


def can_publish(role: str) -> bool:
    return "publish" in capabilities(role)


def can_import(role: str) -> bool:
    return "import" in capabilities(role)


def can_catalogue(role: str) -> bool:
    return "catalogue" in capabilities(role)
