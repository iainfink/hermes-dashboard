"""Basic tests for the Hermes Dashboard server."""

import json
import sqlite3
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from hermes_dashboard.server import create_server


@pytest.fixture
def mock_hermes_dir(tmp_path):
    """Create a minimal mock Hermes directory with test data."""
    # Create state.db
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            user_id TEXT,
            model TEXT,
            model_config TEXT,
            system_prompt TEXT,
            parent_session_id TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            end_reason TEXT,
            message_count INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            reasoning_tokens INTEGER DEFAULT 0,
            billing_provider TEXT,
            billing_base_url TEXT,
            billing_mode TEXT,
            estimated_cost_usd REAL,
            actual_cost_usd REAL,
            cost_status TEXT,
            cost_source TEXT,
            pricing_version TEXT,
            title TEXT
        );

        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL,
            token_count INTEGER,
            finish_reason TEXT,
            reasoning TEXT,
            reasoning_details TEXT,
            codex_reasoning_items TEXT
        );

        INSERT INTO sessions (id, source, model, title, started_at, message_count, tool_call_count, input_tokens, output_tokens, estimated_cost_usd)
        VALUES ('test-session-1', 'cli', 'claude-opus-4', 'Test Session', 1700000000.0, 5, 3, 1000, 500, 0.05);

        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES ('test-session-1', 'user', 'Hello world', 1700000000.0);

        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES ('test-session-1', 'assistant', 'Hi there!', 1700000001.0);
    """)
    conn.close()

    # Create gateway state
    gw = tmp_path / "gateway_state.json"
    gw.write_text(json.dumps({"gateway_state": "running", "pid": 1234}))

    # Create skills dir
    skills = tmp_path / "skills" / "test-skill"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\ndescription: A test skill\n---\n# Test Skill\n")

    return tmp_path


@pytest.fixture
def dashboard_server(mock_hermes_dir):
    """Start a test server and yield the base URL."""
    server = create_server("127.0.0.1", 0, mock_hermes_dir)  # port 0 = random
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def fetch(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


class TestAPI:
    def test_stats(self, dashboard_server):
        data = fetch(f"{dashboard_server}/api/stats")
        assert data["total_sessions"] == 1
        assert data["total_messages"] == 2
        assert data["total_tokens_in"] == 1000
        assert data["total_cost_usd"] == 0.05
        assert data["skills_count"] == 1

    def test_sessions(self, dashboard_server):
        data = fetch(f"{dashboard_server}/api/sessions")
        assert len(data) == 1
        assert data[0]["id"] == "test-session-1"
        assert data[0]["title"] == "Test Session"
        assert data[0]["source"] == "cli"

    def test_session_messages(self, dashboard_server):
        data = fetch(f"{dashboard_server}/api/session/messages?session_id=test-session-1")
        assert len(data) == 2
        assert data[0]["role"] == "user"
        assert data[0]["content"] == "Hello world"

    def test_gateway(self, dashboard_server):
        data = fetch(f"{dashboard_server}/api/gateway")
        assert data["gateway_state"] == "running"

    def test_cron_empty(self, dashboard_server):
        data = fetch(f"{dashboard_server}/api/cron")
        assert data == []

    def test_skills(self, dashboard_server):
        data = fetch(f"{dashboard_server}/api/skills")
        assert len(data) == 1
        assert data[0]["name"] == "test-skill"

    def test_processes_empty(self, dashboard_server):
        data = fetch(f"{dashboard_server}/api/processes")
        assert data == []

    def test_index_html(self, dashboard_server):
        with urllib.request.urlopen(f"{dashboard_server}/") as r:
            html = r.read().decode()
            assert "Hermes Dashboard" in html
