# tg-ai command surface. Recipes are named <area>-<verb>; each area has an
# aggregate (py-check, docs-check) and `just check` runs everything.

set dotenv-load := true

python := ".venv/bin/python"
pip    := "uv pip install --python .venv/bin/python"

# List available recipes.
default:
    @just --list

# --- setup ---------------------------------------------------------------

# Create the virtualenv and install dependencies.
setup:
    uv venv --python 3.12 .venv
    {{ pip }} -r requirements.txt
    @test -f .env || (cp .env.example .env && echo "Created .env - fill in TG_API_ID and TG_API_HASH")

# --- database ------------------------------------------------------------

# Start the local PostgreSQL archive and wait until it is healthy.
db-up:
    docker compose up -d
    @until docker exec tg-ai-postgres pg_isready -U tgai -d tgai >/dev/null 2>&1; do sleep 1; done
    @echo "postgres ready on 127.0.0.1:5434"

# Stop the archive container. Data survives in the named volume.
db-down:
    docker compose down

# Open a psql shell against the archive.
db-psql:
    docker exec -it tg-ai-postgres psql -U tgai -d tgai

# Apply sql/schema.sql without running a sync.
db-schema:
    docker exec -i tg-ai-postgres psql -U tgai -d tgai -v ON_ERROR_STOP=1 < sql/schema.sql

# DESTRUCTIVE: drop the archive volume and recreate it empty.
db-reset:
    docker compose down -v
    just db-up
    just db-schema

# --- telegram ------------------------------------------------------------

# Interactive login. Run this in a real terminal; it asks for an SMS code.
tg-auth:
    {{ python }} auth.py

# Incremental sync: fetch only what is new since the last run.
tg-sync *ARGS:
    {{ python }} sync_db.py {{ ARGS }}

# Full backfill of every private dialog. Slow the first time; resumable.
tg-sync-full *ARGS:
    {{ python }} sync_db.py --full {{ ARGS }}

# Run the MCP server in the foreground (smoke test only; agents spawn it).
tg-serve:
    {{ python }} server.py

# Print the account, session, database and archive status.
tg-status:
    @{{ python }} -c "import asyncio, server; print(asyncio.run(server.tg_whoami()))"

# --- python --------------------------------------------------------------

# Format the codebase.
py-fmt:
    {{ python }} -m black .
    {{ python }} -m ruff check --fix .

# Lint without modifying anything.
py-lint:
    {{ python }} -m ruff check .
    {{ python }} -m black --check .

# Run the test suite.
py-test:
    {{ python }} -m pytest

# Everything Python CI enforces.
py-check: py-lint py-test

# --- docs ----------------------------------------------------------------

# Lint markdown formatting.
docs-lint:
    npx --yes markdownlint-cli2 "**/*.md" "#.venv" "#node_modules"

# Fail if any non-English script appears outside docs/i18n.
docs-english:
    #!/usr/bin/env bash
    set -euo pipefail
    if grep -rlP '[\p{Cyrillic}\p{Greek}\p{Han}\p{Arabic}\p{Hebrew}]' \
        --include='*.md' --include='*.py' --include='*.sql' \
        --exclude-dir=.venv --exclude-dir=.git --exclude-dir=node_modules \
        --exclude-dir=i18n . ; then
        echo "ERROR: non-English text found in the files above (see AGENTS.md rule 1)" >&2
        exit 1
    fi
    echo "english-only: OK"

# Check every relative markdown link resolves to a real file.
docs-links:
    {{ python }} scripts/check_links.py

# Everything documentation CI enforces.
docs-check: docs-english docs-links

# --- aggregate -----------------------------------------------------------

# Run every check CI runs.
check: docs-check py-check

# Register this server with Claude Code (user scope).
mcp-add:
    claude mcp add tg-ai -s user -- {{ justfile_directory() }}/.venv/bin/python {{ justfile_directory() }}/server.py
    @echo "Restart your agent, then call tg_whoami() to verify."

# Remove the registration.
mcp-remove:
    claude mcp remove tg-ai -s user
