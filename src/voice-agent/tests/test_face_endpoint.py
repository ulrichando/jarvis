import asyncio
import logging
from dataclasses import dataclass, field

from voice_client_http_api import VoiceClientHttpApi


@dataclass
class _FakeState:
    output_level: float = 0.0
    face_weights: dict = field(default_factory=dict)


def _make_api(state):
    return VoiceClientHttpApi(
        state=state,
        get_mic_pub=lambda: None,
        get_room=lambda: None,
        restart_agent_unit=lambda: asyncio.sleep(0),
        log=logging.getLogger("test"),
    )


def test_face_route_is_registered():
    api = _make_api(_FakeState())
    app = api.build_app()
    paths = {r.resource.canonical for r in app.router.routes()}
    assert "/face" in paths


def test_face_returns_weights_and_level():
    state = _FakeState(output_level=0.17, face_weights={"target_24": 0.6})
    api = _make_api(state)

    async def go():
        from aiohttp.test_utils import make_mocked_request
        resp = await api.face(make_mocked_request("GET", "/face"))
        import json
        return json.loads(resp.body.decode())

    body = asyncio.run(go())
    assert body["weights"] == {"target_24": 0.6}
    assert body["level"] == 0.17
