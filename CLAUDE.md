# CLAUDE.md

Olas Operate Middleware — FastAPI daemon that manages autonomous-agent services, wallets, and on-chain operations for OLAS. Python 3.11.

## Commands

```bash
poetry install
poetry shell

# tests
tox -e unit-tests                          # fast, no RPC (default for dev)
tox -e integration-tests                   # needs *_TESTNET_RPC env vars
tox -e unit-tests -- tests/test_x.py -v    # single file/test

# linting (CI runs all of these — run locally before commit)
tox -p -e flake8 -e pylint -e black-check -e isort-check -e bandit -e mypy
tox -p -e black-check -e flake8 -e mypy    # quick pass during dev

# daemon
operate daemon                             # or: python -m operate.cli daemon
```

Enable git hooks: `git config core.hooksPath .githooks` (pre-commit auto-formats, pre-push runs lint).

## Critical conventions

- **Linters are blocking in CI.** Use `tox -e unit-tests`, not `poetry run pytest` — only tox installs the package correctly.
- `operate/data/` is auto-generated (contract ABIs). Excluded from lint. Don't edit `contract.py` wrappers directly.
- Password minimum length: 8.
- **Service updates**: stop first, then `PATCH` (partial) or `PUT` (full replace). Hash change triggers redeploy.
- **Funding cooldown**: 5 min default after any funding op to prevent race conditions with agent-side funding requests.

## Deployment-state enum (`DeploymentStatus`)

`1=BUILT`, `2=DEPLOYING`, `3=DEPLOYED`, `4=STOPPING`, `5=STOPPED`. Appears in `/api/v2/service/{id}/deployment` responses and in on-disk `deployment.json`.

## Env-var provision types

Services' `env_variables` use `provision_type`:
- `fixed` — hardcoded in the template
- `computed` — middleware resolves at runtime (safe addresses, RPC URLs, store paths)
- `user` — user-provided via UI (e.g. `GENAI_API_KEY`)

## Directories

- `~/.olas/operate/` (or `$OPERATE_HOME`) = data dir. Subfolders: `services/`, `keys/`, `wallets/`, `settings.json`.
- Wallet hierarchy: Master EOA → Master Safe (2-of-2) → Agent Safe(s) + Agent EOA(s) per service.

## Pull context

- `IMPROVEMENT_PLAN.md` — remaining high-impact work (phases 1 & 2 complete).
- `TESTING.md` — coverage map, gaps, strategy.
- `docs/api.md` — HTTP API reference.
- Version is in `operate/__init__.py`; releases via `.github/workflows/release.yml`.
