# Contributing to Hermes Dashboard

Thanks for your interest in contributing! 🎉

## Quick Start

```bash
# Clone the repo
git clone https://github.com/iainfinkbeiner/hermes-dashboard.git
cd hermes-dashboard

# Install in development mode
pip install -e ".[dev]"

# Run the dashboard
hermes-dashboard
```

## Project Structure

```
hermes-dashboard/
├── src/hermes_dashboard/
│   ├── __init__.py          # Package version
│   ├── cli.py               # CLI entry point (argument parsing)
│   ├── server.py            # HTTP server + API routes
│   └── static/
│       └── index.html       # Dashboard UI (single-page app)
├── tests/
│   └── test_server.py       # API tests
├── pyproject.toml            # Package config
├── README.md
├── LICENSE
└── CONTRIBUTING.md
```

## How It Works

The dashboard is intentionally simple:

- **Backend**: Python stdlib `http.server` — reads Hermes state from `~/.hermes/state.db` (SQLite) and JSON config files. Zero dependencies.
- **Frontend**: Single HTML file with embedded CSS/JS. No build step, no npm, no frameworks.
- **API**: JSON endpoints under `/api/*` that query the Hermes database read-only.

## Development Guidelines

### Keep It Zero-Dependency

The core dashboard must work with **only Python stdlib**. No Flask, no FastAPI, no React. Users should be able to `pip install hermes-dashboard` and have it just work.

If you want to add optional features that need dependencies, gate them behind extras:
```toml
[project.optional-dependencies]
charts = ["plotly"]
```

### Frontend Changes

The UI lives in `src/hermes_dashboard/static/index.html`. It's a single-page app that:
- Fetches from `/api/*` endpoints
- Auto-refreshes every 10 seconds
- Uses CSS custom properties for theming

Feel free to split CSS/JS into separate files if the HTML gets too large, but avoid adding a build step.

### Adding API Endpoints

1. Add a handler function in `server.py` (follow the `api_*` pattern)
2. Register it in the `ROUTES` dict
3. Return JSON-serializable data

### Testing

```bash
pytest tests/
```

## What We'd Love Help With

- 📊 Token usage charts over time
- 🔍 Session search / filtering
- 💰 Cost breakdown by model
- 🎨 Light theme option
- 📱 Mobile responsive improvements
- 🔔 Desktop notifications for long-running tasks
- 📈 Live log tailing
- 🧪 More tests
- 📖 Documentation

## Pull Request Process

1. Fork the repo and create a feature branch
2. Make your changes
3. Test locally with `hermes-dashboard`
4. Submit a PR with a clear description

## Code Style

- Python: Follow PEP 8, use type hints where practical
- HTML/CSS/JS: Keep it clean, comment non-obvious parts
- Commits: Use conventional commits (`feat:`, `fix:`, `docs:`, etc.)

## Questions?

Open an issue or start a discussion. We're friendly! 🤙
