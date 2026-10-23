from dcc_backend_common.logger import get_logger
from fastapi import APIRouter

from anony_mate_api.models.blacklist_type import BlacklistType

logger = get_logger("blacklist_router")


BLACKLISTS: dict[BlacklistType, list[str]] = {
    BlacklistType.default: ["acme", "widget"],
    BlacklistType.legal: ["court", "plaintiff", "defendant", "judge"],
}


def create_router() -> APIRouter:
    logger.info("Creating blacklist router")
    router: APIRouter = APIRouter(prefix="/blacklist", tags=["entities"])

    @router.get("/{type_name}")
    async def blacklist(type_name: BlacklistType) -> list[str]:
        """The blacklist for an entity type: words that must never be
        redacted, e.g. country names for `location`."""
        return BLACKLISTS[type_name]

    logger.info("blacklist router configured")

    return router
