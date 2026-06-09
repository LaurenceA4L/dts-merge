# Contributing to dts-merge

## Development Setup

```bash
git clone https://github.com/LaurenceA4L/dts-merge.git
cd dts-merge
python -m venv .venv

# Linux/macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
```

## Running Tests

```bash
pytest -v
```

## Pull Requests

- Target the `main` branch.
- Include tests for any new merge behaviour.
- Keep commits focused — one logical change per commit.

## Reporting Bugs

Use the GitHub issue tracker with the Bug Report template.
