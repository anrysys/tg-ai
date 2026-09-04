"""Shared test fixtures.

The suite is deliberately offline: it never contacts Telegram and never needs
a database. The rules that keep the account safe are pure functions precisely
so they can be proven here.
"""

import os

import pytest

import tg_ai.config


@pytest.fixture
def clean_env(monkeypatch):
    """Isolate configuration tests from the environment and from ``.env``.

    Two sources have to be neutralised. Deleting the variables is not enough:
    ``load_config`` also reads the developer's real ``.env`` from the repository
    root, so a test would pass or fail depending on whether that file happens to
    exist. Stubbing ``load_dotenv`` makes the result depend only on what the
    test sets.
    """
    for name in list(os.environ):
        if name.startswith("TG_") or name == "DATABASE_URL":
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tg_ai.config, "load_dotenv", lambda *args, **kwargs: False)
    return monkeypatch
