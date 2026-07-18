"""LG webOS TV controller — aiowebostv over ws://<ip>:3000 (wss :3001 fallback).

Pairing: first connect() with client_key=None makes the TV show an on-screen
accept prompt; on accept we persist client.client_key to
~/.jarvis/iot-devices/webos/<mac>.json and reconnect silently forever after.
Clients are cached per TV (connect-once / hold / reuse — aiowebostv holds a
5s-heartbeat websocket), NOT reconnected per command.

Power-on: the websocket is dead while the TV is off, so `power_on` sends a
Wake-on-LAN magic packet to the subnet broadcast, then polls connect ~8s.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import tempfile
import time
from pathlib import Path

from aiowebostv import WebOsClient, WebOsTvPairError
from wakeonlan import send_magic_packet

from iot.controllers import Controller
from iot.models import Device

log = logging.getLogger("jarvis.iot.webos")


class NotPairedError(RuntimeError):
    pass


class WebOSController(Controller):
    name = "webos"

    def __init__(self, key_dir: Path | None = None, connect_timeout: float = 5.0):
        self.key_dir = Path(key_dir) if key_dir else (
            Path.home() / ".jarvis" / "iot-devices" / "webos")
        self.connect_timeout = connect_timeout
        self._clients: dict[str, WebOsClient] = {}

    # -- framework --------------------------------------------------------
    def can_control(self, device: Device) -> bool:
        b = (device.brand or "").lower()
        h = (device.control_hint or "").lower()
        return "webos" in b or "webos" in h or (b.startswith("lg") and device.type == "tv")

    def capabilities(self, device: Device) -> list[str]:
        return ["power", "volume", "mute", "apps", "inputs", "media"]

    # -- client-key persistence -------------------------------------------
    def _dev_key(self, device: Device) -> str:
        return (device.mac or device.ip).lower().replace(":", "-")

    def _key_path(self, device: Device) -> Path:
        return self.key_dir / f"{self._dev_key(device)}.json"

    def _load_key(self, device: Device) -> str | None:
        try:
            return json.loads(self._key_path(device).read_text()).get("client_key")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_key(self, device: Device, client_key: str) -> None:
        self.key_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.key_dir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump({"client_key": client_key, "ip": device.ip, "mac": device.mac}, f)
        os.replace(tmp, self._key_path(device))

    # -- connect / pair -----------------------------------------------------
    async def connect(self, device: Device, **params) -> dict:
        """Pair (or re-verify) — first call makes the TV show an accept prompt."""
        old = self._clients.pop(self._dev_key(device), None)
        if old is not None:
            try:
                await old.disconnect()
            except Exception:  # noqa: BLE001
                pass
        client = WebOsClient(device.ip, client_key=self._load_key(device),
                             connect_timeout=self.connect_timeout)
        try:
            await client.connect()
        except WebOsTvPairError:
            return {"ok": False, "status": 428,
                    "error": "Pairing rejected or timed out — press Accept on the "
                             "TV screen, then call connect again."}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "status": 502,
                    "error": f"could not reach the TV at {device.ip}: {e} — if it's "
                             "off, send action power_on first (Wake-on-LAN)."}
        if client.client_key:
            self._save_key(device, client.client_key)
        self._clients[self._dev_key(device)] = client
        return {"ok": True, "paired": True, "ip": device.ip}

    async def _get_client(self, device: Device) -> WebOsClient:
        ckey = self._dev_key(device)
        client = self._clients.get(ckey)
        if client is not None and client.is_connected():
            return client
        stored = self._load_key(device)
        if not stored:
            raise NotPairedError(
                f"TV not paired — POST /devices/{device.key}/connect and press "
                "Accept on the TV screen.")
        client = WebOsClient(device.ip, client_key=stored,
                             connect_timeout=self.connect_timeout)
        await client.connect()
        self._clients[ckey] = client
        return client

    # -- commands -----------------------------------------------------------
    async def command(self, device: Device, action: str, **params) -> dict:
        if action == "power_on":
            try:
                wait = float(params.get("wait", 8.0))
            except (TypeError, ValueError):
                wait = 8.0
            return await self._power_on(device, wait=wait)
        try:
            client = await self._get_client(device)
        except NotPairedError as e:
            return {"ok": False, "status": 428, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "status": 502,
                    "error": f"could not reach the TV at {device.ip}: {e} — if it's "
                             "off, send action power_on first."}
        try:
            result = await self._dispatch(client, action, params)
        except LookupError:
            return {"ok": False, "status": 400, "error": f"unknown action '{action}'"}
        except KeyError as e:
            return {"ok": False, "status": 400, "error": f"missing parameter {e} for '{action}'"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "status": 502, "error": f"{action} failed: {e}"}
        if not isinstance(result, (dict, list, str, int, float, bool, type(None))):
            result = str(result)
        return {"ok": True, "action": action, "result": result}

    async def _dispatch(self, client: WebOsClient, action: str, params: dict):
        if action == "power_off":
            return await client.power_off()
        if action == "volume_up":
            return await client.volume_up()
        if action == "volume_down":
            return await client.volume_down()
        if action == "set_volume":
            return await client.set_volume(int(params["volume"]))
        if action == "mute":
            return await client.set_mute(bool(params.get("mute", True)))
        if action == "launch_app":
            return await client.launch_app(params["app_id"])
        if action == "get_apps":
            return await client.get_apps()
        if action == "set_input":
            return await client.set_input(params.get("input_id") or params["input"])
        if action == "get_inputs":
            return await client.get_inputs()
        if action == "play":
            return await client.play()
        if action == "pause":
            return await client.pause()
        if action == "stop":
            return await client.stop()
        if action == "button":
            return await client.button(params["name"])
        if action == "get_state":
            state: dict = {}
            for getter in ("get_power_state", "get_current_app", "get_volume", "get_muted"):
                try:
                    state[getter.removeprefix("get_")] = await getattr(client, getter)()
                except Exception as e:  # noqa: BLE001
                    state[getter.removeprefix("get_")] = f"error: {e}"
            return state
        raise LookupError(action)

    # -- power on (Wake-on-LAN) ----------------------------------------------
    def _broadcast(self, device: Device) -> str:
        try:
            return str(ipaddress.ip_network(f"{device.ip}/24", strict=False).broadcast_address)
        except ValueError:
            return "255.255.255.255"

    async def _power_on(self, device: Device, wait: float = 8.0) -> dict:
        if not device.mac:
            return {"ok": False, "status": 400,
                    "error": "no MAC address known for this TV — cannot Wake-on-LAN"}
        broadcast = self._broadcast(device)
        await asyncio.to_thread(send_magic_packet, device.mac, ip_address=broadcast)
        if not self._load_key(device):
            return {"ok": True, "wol_sent": True, "connected": False,
                    "note": "magic packet sent; TV not paired yet so boot can't be confirmed"}
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                await self._get_client(device)
                return {"ok": True, "wol_sent": True, "connected": True}
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1.0)
        return {"ok": True, "wol_sent": True, "connected": False,
                "note": f"magic packet sent; TV didn't answer within {wait:.0f}s "
                        "(it may still be booting)"}
