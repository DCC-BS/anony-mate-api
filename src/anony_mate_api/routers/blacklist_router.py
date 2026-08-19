from enum import Enum

from dcc_backend_common.logger import get_logger
from fastapi import APIRouter

logger = get_logger("blacklist_router")


class BlacklistType(Enum):
    default = "default"
    legal = "legal"


BLACKLISTS: dict[BlacklistType, list[str]] = {
    BlacklistType.default: ["acme", "widget"],
    BlacklistType.legal: ["court", "plaintiff", "defendant", "judge"],
}


def create_router() -> APIRouter:
    logger.info("Creating blacklist router")
    router: APIRouter = APIRouter(prefix="/blacklist")

    @router.get("/{type_name}")
    async def blacklist(type_name: BlacklistType) -> list[str]:
        return BLACKLISTS[type_name]

    logger.info("blacklist router configured")

    return router
