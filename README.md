# ⚡ Hermes Dashboard

A sleek, zero-dependency local web dashboard for monitoring [Hermes Agent](https://github.com/hermes-agent/hermes). See your sessions, cron jobs, skills, token usage, and costs — all in one place.

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)
![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)

<!-- 
TODO: Add screenshot
![Hermes Dashboard Screenshot](docs/screenshot.png) 
-->

## Features

- 🗂 **Sessions** — View all sessions (CLI, gateway, subagents) with live/ended status
- 📊 **Stats at a Glance** — Total sessions, messages, tool calls, tokens, estimated cost
- ⏰ **Cron Jobs** — Monitor scheduled jobs and their status
- ⚙️ **Background Processes** — See what's running
- 🧠 **Skills Library** — Browse all installed skills by category
- 💬 **Message Viewer** — Click any session to read the full conversation
- 🌐 **Gateway Status** — Live online/offline indicator
- 🔄 **Auto-Refresh** — Updates every 10 seconds
- 🎨 **Dark Terminal Aesthetic** — Because we're terminal people

## Install

```bash
pip install hermes-dashboard
```

Or install from source:

```bash
git clone https://github.com/iainfinkbeiner/hermes-dashboard.git
cd hermes-dashboard
pip install -e .
```

## Usage

```bash
# Just run it — auto-detects ~/.hermes and opens your browser
hermes-dashboard

# Custom port
hermes-dashboard -p 8080

# Custom Hermes directory
hermes-dashboard --hermes-dir /path/to/.hermes

# Don't auto-open browser
hermes-dashboard --no-open
```

That's it. One command, dashboard in your browser.

## How It Works

The dashboard reads directly from your local Hermes Agent data:

| Source | What |
|--------|------|
| `~/.hermes/state.db` | Sessions, messages, token counts, costs (SQLite) |
| `~/.hermes/gateway_state.json` | Gateway online/offline status |
| `~/.hermes/cron/jobs.json` | Scheduled cron jobs |
| `~/.hermes/skills/` | Installed skill library |

**Read-only** — the dashboard never writes to your Hermes data. It opens the SQLite database in read-only mode.

**Zero dependencies** — built entirely on Python stdlib (`http.server`, `sqlite3`, `json`). No Flask, no React, no npm. Just Python.

**Local only** — binds to `127.0.0.1` by default. Your data never leaves your machine.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `HERMES_DASHBOARD_PORT` | `7777` | Server port |
| `HERMES_DIR` | `~/.hermes` | Hermes data directory |

## API Endpoints

The dashboard exposes a JSON API you can use from scripts:

```bash
# Get overview stats
curl http://localhost:7777/api/stats

# List recent sessions
curl http://localhost:7777/api/sessions?limit=10

# Get messages from a session
curl http://localhost:7777/api/session/messages?session_id=SESSION_ID

# Gateway status
curl http://localhost:7777/api/gateway

# Cron jobs
curl http://localhost:7777/api/cron

# Skills list
curl http://localhost:7777/api/skills

# Background processes
curl http://localhost:7777/api/processes
```

## Requirements

- Python 3.9+
- [Hermes Agent](https://github.com/hermes-agent/hermes) installed and run at least once (to create `state.db`)

## Contributing

We'd love your help! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

**Ideas we'd love PRs for:**
- 📊 Token usage charts over time
- 🔍 Session search and filtering
- 💰 Cost breakdown by model
- 🌙 Light/dark theme toggle
- 📱 Better mobile layout
- 🔔 Desktop notifications
- 📈 Live log tailing
- 🧪 More tests

## License

MIT — see [LICENSE](LICENSE)
