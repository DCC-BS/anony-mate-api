from enum import Enum


class EntityType(Enum):
    """The entity type presets the API serves under ``GET /entity_types/{preset}``."""

    #: The types an everyday document needs redacted.
    default = "default"
    #: The types a legal document needs redacted.
    legal = "legal"
    #: Every type the model knows.
    full = "full"
