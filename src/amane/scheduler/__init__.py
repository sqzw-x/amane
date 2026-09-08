"""任务 handler 协议见 amane.handlers.protocol."""

from .clouddrive import CloudDriveChange, CloudDriveRoute, local_for, match_route
from .service import WatcherService
from .watcher import FileWatcher

__all__ = [
    "CloudDriveChange",
    "CloudDriveRoute",
    "FileWatcher",
    "WatcherService",
    "local_for",
    "match_route",
]
