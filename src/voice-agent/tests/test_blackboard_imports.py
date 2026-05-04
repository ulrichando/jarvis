"""Smoke test for the blackboard package and its Redis dependency."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_blackboard_package_imports():
    import blackboard  # noqa: F401


def test_redis_client_constructible():
    import redis
    r = redis.Redis(host="localhost", port=6379, decode_responses=True)
    # ping requires a running server; this validates the lib is functional
    assert r.ping() is True
