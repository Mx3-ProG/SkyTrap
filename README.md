# SkyTrap

Local-first autonomous coding agent. SkyTrap inspects a Git repository, plans a
change, calls real workspace tools, repairs verification failures, and checkpoints a
verified result on a dedicated task branch.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally
- `qwen2.5-coder:7b` (default) or another configured Ollama coding model
- [uv](https://github.com/astral-sh/uv)
- Git

## Install

```bash
git clone https://github.com/Mx3-ProG/SkyTrap.git
cd SkyTrap
uv sync
uv pip install -e .
ollama pull qwen2.5-coder:7b
```

## Autonomous task

The target repository must have a clean Git working tree. SkyTrap creates
`skytrap/task-<task-id>`, never pushes automatically, and only reports success after at
least one real lint, typecheck, test, or build command succeeds.

```bash
ollama serve
skytrap agent run /absolute/path/to/project \
  "Corrige le bug de login, lance les tests et vérifie le build"
```

Task state is persisted under `~/.skytrap/tasks` by default. Override it with
`SKYTRAP_STATE_DIR` when embedding or testing SkyTrap.

```bash
skytrap agent status
skytrap agent status <task-id>
skytrap agent stop <task-id>
skytrap agent resume <task-id>
skytrap agent rollback <task-id>
```

The existing interactive assistant remains available with `skytrap`; the autonomous
commands above use `skytrap.autonomy.AgentLoop`, not the legacy turn loop.

## Validation

```bash
uv run pytest -q
cd frontend && npm run build
```
