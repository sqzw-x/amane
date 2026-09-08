from fastapi import APIRouter

from .actors import router as actors_router
from .agent import router as agent_router
from .comments import router as comments_router
from .config import router as config_router
from .facets import router as facets_router
from .feeds import router as feeds_router
from .files import router as files_router
from .health import router as health_router
from .libraries import router as libraries_router
from .media import router as media_router
from .metadata import router as metadata_router
from .plugins import router as plugins_router
from .resources import router as resources_router
from .schedules import router as schedules_router
from .system import router as system_router
from .tasks import router as tasks_router
from .webhooks import router as webhooks_router
from .ws import router as ws_router

API_PREFIX = "/api"

router = APIRouter(prefix=API_PREFIX)
router.include_router(health_router)
router.include_router(config_router)
router.include_router(files_router)
router.include_router(media_router)
router.include_router(metadata_router)
router.include_router(plugins_router)
router.include_router(actors_router)
router.include_router(facets_router)
router.include_router(feeds_router)
router.include_router(comments_router)
router.include_router(resources_router)
router.include_router(schedules_router)
router.include_router(system_router)
router.include_router(tasks_router)
router.include_router(libraries_router)
router.include_router(webhooks_router)
router.include_router(agent_router)
router.include_router(ws_router)
