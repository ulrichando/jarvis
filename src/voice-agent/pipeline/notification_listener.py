"""D-Bus session-bus monitor for desktop notifications — so JARVIS can read the
user's notifications WITHOUT a screenshot.

Eavesdrops org.freedesktop.Notifications.Notify method calls (the way every app
posts a notification) and records {app, summary, body} into the notification
store. Runs as a --user systemd service:  python -m pipeline.notification_listener
Reconnects on bus loss. A notification daemon must be present (it is — this only
observes, it does not own the service name).
"""
from __future__ import annotations

import logging
import time

from jeepney import MessageType
from jeepney.bus_messages import MatchRule, Monitoring
from jeepney.io.blocking import open_dbus_connection

from pipeline import notification_store

logger = logging.getLogger("jarvis.notifications")


def _run_once() -> None:
    conn = open_dbus_connection(bus="SESSION")
    rule = MatchRule(
        type="method_call",
        interface="org.freedesktop.Notifications",
        member="Notify",
    )
    # BecomeMonitor delivers only messages matching the rule(s) — i.e. Notify calls.
    conn.send_and_get_reply(Monitoring().BecomeMonitor([rule.serialise()]))
    logger.info("[notifications] monitor armed")
    while True:
        msg = conn.receive()
        if msg.header.message_type != MessageType.method_call:
            continue
        body = msg.body
        # Notify(app_name, replaces_id, app_icon, summary, body, actions, hints, timeout)
        if len(body) < 5:
            continue
        notification_store.append(body[0], body[3], body[4])
        logger.debug("[notifications] captured app=%s summary=%s", body[0], body[3])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            _run_once()
        except Exception as exc:  # noqa: BLE001 — reconnect on ANY bus/parse error
            logger.warning("[notifications] monitor error: %s — reconnecting in 5s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
