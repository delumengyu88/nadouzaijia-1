# 那豆在架

Backend skeleton for the 那豆在架 copyright-monitoring lookup service. See [docs/DESIGN.md](docs/DESIGN.md) for the data model, the search permission rules, and what this PR deliberately leaves out (including member ZIP download).

## Local stack

```bash
docker compose up --build -d
docker compose exec api python -m nadou.seed
curl -s "http://localhost:8000/api/v1/programs/search?name=%E7%A4%BA%E4%BE%8B%E5%89%A7%E9%9B%86"
```

`docker-compose.yml` starts Postgres with a local-only password. Copy `.env.example` if you run the API on the host instead of in Compose.

## Tests

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
```
