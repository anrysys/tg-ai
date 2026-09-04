"""Configuration validation. Proves SPEC-SEC-004."""

import pytest

from tg_ai.config import ConfigError, load_config

VALID_DB = "postgresql://tgai:tgai@127.0.0.1:5434/tgai"


def test_missing_telegram_credentials_are_reported_with_a_next_step(clean_env):
    clean_env.setenv("DATABASE_URL", VALID_DB)
    with pytest.raises(ConfigError, match="my.telegram.org"):
        load_config()


def test_database_only_tools_can_skip_telegram_credentials(clean_env):
    clean_env.setenv("DATABASE_URL", VALID_DB)
    config = load_config(require_telegram=False)
    assert config.database_url == VALID_DB


def test_missing_database_url_is_reported(clean_env):
    clean_env.setenv("TG_API_ID", "1")
    clean_env.setenv("TG_API_HASH", "hash")
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        load_config()


def test_non_numeric_api_id_is_rejected(clean_env):
    clean_env.setenv("TG_API_ID", "not-a-number")
    clean_env.setenv("TG_API_HASH", "hash")
    clean_env.setenv("DATABASE_URL", VALID_DB)
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config()


def test_session_name_may_not_be_a_path(clean_env):
    clean_env.setenv("TG_API_ID", "1")
    clean_env.setenv("TG_API_HASH", "hash")
    clean_env.setenv("DATABASE_URL", VALID_DB)
    clean_env.setenv("TG_SESSION_NAME", "../../etc/passwd")
    with pytest.raises(ConfigError, match="bare file name"):
        load_config()


def test_session_paths_are_derived_from_the_session_name(clean_env):
    clean_env.setenv("TG_API_ID", "1")
    clean_env.setenv("TG_API_HASH", "hash")
    clean_env.setenv("DATABASE_URL", VALID_DB)
    clean_env.setenv("TG_SESSION_NAME", "tg_session")
    config = load_config()
    assert config.session_file.name == "tg_session.session"
    # The sync clone must be a different file, or the two processes deadlock.
    assert config.sync_session_file.name == "tg_session.sync.session"
    assert config.sync_session_file != config.session_file


def test_expected_username_is_normalised_without_the_at_sign(clean_env):
    clean_env.setenv("TG_API_ID", "1")
    clean_env.setenv("TG_API_HASH", "hash")
    clean_env.setenv("DATABASE_URL", VALID_DB)
    clean_env.setenv("TG_EXPECTED_USERNAME", "@anrysys")
    assert load_config().expected_username == "anrysys"
