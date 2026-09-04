"""tg-ai: a local MCP server that drives a personal Telegram account.

Three processes share this package:

* ``auth.py``    - interactive MTProto login, writes the session file.
* ``sync_db.py`` - dumps private chat history into PostgreSQL.
* ``server.py``  - the MCP stdio server the AI agent talks to.

Authoritative behaviour is specified in ``docs/10-product/srs.md``.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
