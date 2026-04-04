"""Remote session manager — shim for brain compatibility."""
from src.remote.RemoteSessionManager import RemoteSessionManager

_instance = None

def get_remote_session_manager():
    global _instance
    if _instance is None:
        _instance = RemoteSessionManager()
    return _instance
