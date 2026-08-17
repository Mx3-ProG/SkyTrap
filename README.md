# SkyTrap

Local-first, open source AI coding agent — usable directly from the terminal.

SkyTrap is being built progressively: this is milestone V0.1, a minimal CLI that
detects the current workspace and talks to a local Ollama model. No filesystem,
git, or shell tools yet — those come one at a time in later iterations.

## Requirements

- Python >= 3.11
- [Ollama](https://ollama.com) running locally with `qwen2.5-coder:7b` pulled
- [uv](https://github.com/astral-sh/uv)

## Install

```bash
cd ~/Empire/SkyTrap
uv pip install -e .
```

## Usage

From any project directory:

```bash
cd ~/Empire/some-project
skytrap
```
