"""The Telethon constructor contract and the Client Identity.

Proves SPEC-SEC-007 (identity is configured, honest and stable) and
SPEC-SEC-008 (every risky Telethon default is pinned explicitly).

``build_client`` is exercised through a recording stand-in rather than the
real ``TelegramClient`` because constructing one creates a session file on
disk, and this suite never touches the filesystem or the network.
"""

from datetime import time as dt_time

import pytest
from source_scan import find_in_sources

import tg_ai.tg_client as tg_client
from tg_ai.config import Config
from tg_ai.tg_client import build_client

#: Every parameter the anti-ban contract pins, with the value it must carry.
#: A default inherited from Telethon is a defect, so each one is asserted by
#: name rather than by "whatever the library does".
REQUIRED_CONSTRUCTOR_ARGUMENTS = {
    "flood_sleep_threshold": 0,
    "receive_updates": False,
    "catch_up": False,
    "request_retries": 1,
    "connection_retries": 2,
    "retry_delay": 5,
    "auto_reconnect": True,
    "entity_cache_limit": 500,
}

IDENTITY_ARGUMENTS = (
    "device_model",
    "system_version",
    "app_version",
    "lang_code",
    "system_lang_code",
)


def make_config(**overrides) -> Config:
    defaults = {
        "api_id": 12345,
        "api_hash": "hash",
        "session_name": "tg_session",
        "expected_username": None,
        "database_url": "postgresql://localhost/x",
        "sync_include_bots": False,
        "device_model": "Desktop",
        "system_version": "Linux 7.0",
        "app_version": "tg-ai 1.0",
        "lang_code": "es",
        "system_lang_code": "es",
        "timezone": "Europe/Kyiv",
        "quiet_start": dt_time(1, 0),
        "quiet_end": dt_time(8, 0),
    }
    return Config(**{**defaults, **overrides})


class RecordingClient:
    """Stands in for ``GuardedClient`` and remembers how it was called."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


@pytest.fixture
def recorded(monkeypatch):
    """Return a callable that builds a client and hands back the record."""
    monkeypatch.setattr(tg_client, "GuardedClient", RecordingClient)

    def build(config=None, **kwargs):
        return build_client(config if config is not None else make_config(), **kwargs)

    return build


@pytest.mark.parametrize(("name", "value"), sorted(REQUIRED_CONSTRUCTOR_ARGUMENTS.items()))
def test_every_risky_telethon_default_is_pinned(recorded, name, value):
    client = recorded()
    assert name in client.kwargs, f"{name} is left at Telethon's default"
    assert client.kwargs[name] is value or client.kwargs[name] == value


def test_flood_waits_are_never_slept_through(recorded):
    # Called out on its own because it is the one value whose wrong setting is
    # invisible: Telethon's default of 60 silently retries every short flood
    # wait, which is what AGENTS.md forbids and what the kill switch counts.
    assert recorded().kwargs["flood_sleep_threshold"] == 0


def test_identity_comes_from_config_not_from_telethon(recorded):
    config = make_config(
        device_model="Laptop",
        system_version="Linux 6.8",
        app_version="tg-ai 9.9",
        lang_code="pt",
        system_lang_code="pt",
    )
    kwargs = recorded(config).kwargs
    assert kwargs["device_model"] == "Laptop"
    assert kwargs["system_version"] == "Linux 6.8"
    assert kwargs["app_version"] == "tg-ai 9.9"
    assert kwargs["lang_code"] == "pt"
    assert kwargs["system_lang_code"] == "pt"


def test_identity_is_identical_for_the_primary_session_and_the_clone(recorded):
    config = make_config()
    primary = recorded(config)
    clone = recorded(config, session_base=config.sync_session_path)

    assert primary.args[0] != clone.args[0], "the two sessions must be different files"
    for name in IDENTITY_ARGUMENTS:
        assert primary.kwargs[name] == clone.kwargs[name], (
            f"{name} differs between the primary session and the sync clone; "
            "initConnection would then describe two different clients on one account"
        )


def test_the_app_version_never_claims_to_be_an_official_client(recorded):
    # Posing as Telegram Desktop under a personal api_id is a detectable
    # inconsistency, and one that can only be deliberate.
    app_version = recorded().kwargs["app_version"].lower()
    assert "telegram" not in app_version
    assert "tg-ai" in app_version


# --- Update suppression (SPEC-SEC-009) ------------------------------------

#: How a Telethon event handler gets registered. With ``receive_updates=False``
#: a handler silently never fires, so one appearing here is either dead code or
#: - far worse - a change about to re-enable updates to make it work.
EVENT_HANDLER_MARKERS = (
    "add_event_handler",
    "telethon.events",
    "from telethon import events",
    "events.NewMessage",
    "events.MessageEdited",
    "events.ChatAction",
    "events.Raw",
)


@pytest.mark.parametrize("marker", EVENT_HANDLER_MARKERS)
def test_the_codebase_registers_no_telethon_event_handlers(marker):
    hits = find_in_sources(marker)
    assert not hits, (
        f"{marker!r} appears in the source tree:\n  " + "\n  ".join(hits) + "\n"
        "Event handlers are forbidden here, not merely unused: build_client "
        "passes receive_updates=False, so a handler never fires, and turning "
        "updates back on to 'fix' it subscribes the account to every message "
        "in every group it belongs to (ADR-0009)."
    )
