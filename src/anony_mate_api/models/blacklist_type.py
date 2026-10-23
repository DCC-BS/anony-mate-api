from enum import Enum


class BlacklistType(Enum):
    """The blacklist presets the API serves under ``GET /blacklist/{type}``."""

    #: The blacklist an everyday document needs.
    default = "default"
    #: The blacklist a legal document needs.
    legal = "legal"
