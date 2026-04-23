# Sahay — backend

FastAPI + Postgres + Redis + Anthropic. Deployed to Railway.

## Local development

### Prerequisites

- Python 3.12 (`pyenv install 3.12.7` recommended)
- Docker (for local Postgres + Redis)
- A Resend API key + Anthropic API key in `.env`

### Setup

```bash
# 1. Clone + cd
cd backend

# 2. Create venv + install
python -m venv .venv
. .venv/Scripts/activate           # Windows bash
# . .venv/bin/activate             # macOS/Linux
pip install -e ".[dev]"

# 3. Copy env, fill secrets
cp .env.example .env
# edit .env

# 4. Spin up local Postgres + Redis (Docker)
docker run -d --name sahay-pg -p 5432:5432 \
    -e POSTGRES_PASSWORD=sahay -e POSTGRES_DB=sahay postgres:16
docker run -d --name sahay-redis -p 6379:6379 redis:7-alpine

# 5. Run migrations
alembic upgrade head

# 6. (one-time, after migrations) apply RLS policies
psql "postgresql://postgres:sahay@localhost:5432/sahay" -f ../infra/postgres-rls.sql

# 7. Start the server
uvicorn sahay.main:app --reload --port 8080
```

Open `http://localhost:8080/docs` for the interactive API.

## Verifying the skeleton works

```bash
# Liveness
curl -s http://localhost:8080/health
# → {"status":"ok","version":"0.1.0"}

# Readiness (checks DB)
curl -s http://localhost:8080/ready
# → {"status":"ready"}
```

## Tests

```bash
pytest
```

## Deploy

Push to `main`. GitHub Actions builds the Docker image; Railway deploys.
First-time setup: connect Railway service to repo, set env vars from `.env.example`,
attach Neon Postgres + Upstash Redis.

## Architecture

See [../docs/superpowers/specs/2026-04-23-sahay-design.md](../docs/superpowers/specs/2026-04-23-sahay-design.md).
