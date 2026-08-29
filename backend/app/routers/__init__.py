from .projects import router as projects_router
from .elements import router as elements_router
from .cases import router as cases_router
from .generate import router as generate_router
from .heal import router as heal_router
from .executions import router as executions_router
from .reports import router as reports_router
from .files import router as files_router

__all__ = [
    "projects_router",
    "elements_router",
    "cases_router",
    "generate_router",
    "heal_router",
    "executions_router",
    "reports_router",
    "files_router",
]
