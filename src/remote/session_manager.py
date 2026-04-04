"""Remote session manager — singleton accessor for brain compatibility."""
from src.remote.RemoteSessionManager import RemoteSessionManager

_instance: RemoteSessionManager | None = None


def get_remote_session_manager() -> RemoteSessionManager:
    """Get or create the global RemoteSessionManager singleton."""
    global _instance
    if _instance is None:
        _instance = RemoteSessionManager()
    return _instance


def set_remote_session_manager(manager: RemoteSessionManager) -> None:
    """Replace the global RemoteSessionManager (used by web server at startup)."""
    global _instance
    _instance = manager
