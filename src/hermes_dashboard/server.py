"""
Hermes Dashboard Server — Zero-dependency HTTP server + API.

Reads Hermes Agent state from SQLite DB and JSON config files.
No external dependencies beyond Python stdlib.
"""

import json
import sqlite3
from datetime import datetime, timezone
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Resolved at runtime via CLI
HERMES_DIR: Path = None

STATIC_DIR = Path(__file__).parent / "static"


def get_db():
    """Open a read-only connection to the Hermes state database."""
    db_path = HERMES_DIR / "state.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _ts(epoch):
    """Convert epoch timestamp to ISO format."""
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# API Handlers
# ---------------------------------------------------------------------------

def api_sessions(params):
    limit = int(params.get("limit", [30])[0])
    db = get_db()
    rows = db.execute(
        """SELECT id, source, model, title, started_at, ended_at, end_reason,
                  message_count, tool_call_count, input_tokens, output_tokens,
                  cache_read_tokens, estimated_cost_usd, parent_session_id
           FROM sessions ORDER BY started_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    db.close()
    return [
        {
            "id": r["id"],
            "source": r["source"],
            "model": r["model"],
            "title": r["title"],
            "started_at": _ts(r["started_at"]),
            "ended_at": _ts(r["ended_at"]),
            "end_reason": r["end_reason"],
            "message_count": r["message_count"],
            "tool_call_count": r["tool_call_count"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cache_read_tokens": r["cache_read_tokens"],
            "estimated_cost_usd": r["estimated_cost_usd"],
            "is_subagent": r["parent_session_id"] is not None,
        }
        for r in rows
    ]


def api_session_messages(params):
    session_id = params.get("session_id", [None])[0]
    if not session_id:
        return {"error": "session_id required"}
    db = get_db()
    rows = db.execute(
        """SELECT role, content, tool_name, timestamp, finish_reason
           FROM messages WHERE session_id = ? ORDER BY timestamp""",
        (session_id,),
    ).fetchall()
    db.close()
    result = []
    for r in rows:
        content = r["content"] or ""
        if len(content) > 3000:
            content = content[:3000] + "\n... [truncated]"
        result.append({
            "role": r["role"],
            "content": content,
            "tool_name": r["tool_name"],
            "timestamp": _ts(r["timestamp"]),
            "finish_reason": r["finish_reason"],
        })
    return result


def api_gateway(_params):
    gw = HERMES_DIR / "gateway_state.json"
    if gw.exists():
        try:
            return json.loads(gw.read_text())
        except Exception:
            pass
    return {"gateway_state": "unknown"}


def api_cron_jobs(_params):
    jobs_file = HERMES_DIR / "cron" / "jobs.json"
    if jobs_file.exists():
        try:
            data = json.loads(jobs_file.read_text())
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return list(data.values())
        except Exception:
            pass
    return []


def api_skills(_params):
    skills_dir = HERMES_DIR / "skills"
    skills = []
    if skills_dir.exists():
        for item in sorted(skills_dir.rglob("SKILL.md")):
            rel = item.relative_to(skills_dir)
            parts = rel.parts
            name = parts[-2] if len(parts) > 1 else parts[0]
            category = str(rel.parent.parent) if len(parts) > 2 else (
                str(rel.parent) if len(parts) > 1 else ""
            )
            desc = ""
            try:
                for line in item.read_text().split("\n")[:20]:
                    if line.strip().startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                        break
            except Exception:
                pass
            skills.append({"name": name, "category": category, "description": desc})
    return skills


def api_stats(_params):
    db = get_db()
    row = db.execute(
        """SELECT
            count(*) as total,
            sum(CASE WHEN ended_at IS NULL THEN 1 ELSE 0 END) as active,
            coalesce(sum(input_tokens), 0) as tokens_in,
            coalesce(sum(output_tokens), 0) as tokens_out,
            coalesce(sum(estimated_cost_usd), 0) as cost,
            coalesce(sum(tool_call_count), 0) as tools
           FROM sessions"""
    ).fetchone()
    msg_count = db.execute("SELECT count(*) FROM messages").fetchone()[0]
    db.close()

    cron_jobs = api_cron_jobs(None)
    active_crons = len([j for j in cron_jobs if j.get("status") == "active" or j.get("enabled", True)])

    return {
        "total_sessions": row["total"],
        "active_sessions": row["active"],
        "total_messages": msg_count,
        "total_tokens_in": row["tokens_in"],
        "total_tokens_out": row["tokens_out"],
        "total_cost_usd": round(row["cost"], 4),
        "total_tool_calls": row["tools"],
        "cron_jobs_active": active_crons,
        "cron_jobs_total": len(cron_jobs),
        "skills_count": len(api_skills(None)),
    }


def api_processes(_params):
    registry = HERMES_DIR / "process_registry.json"
    if registry.exists():
        try:
            return json.loads(registry.read_text())
        except Exception:
            pass
    return []


ROUTES = {
    "/api/sessions": api_sessions,
    "/api/session/messages": api_session_messages,
    "/api/gateway": api_gateway,
    "/api/cron": api_cron_jobs,
    "/api/skills": api_skills,
    "/api/stats": api_stats,
    "/api/processes": api_processes,
}


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the dashboard UI and API endpoints."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # API routes
        if path in ROUTES:
            try:
                data = ROUTES[path](params)
                body = json.dumps(data, default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return

        # Static files
        if path == "/" or path == "":
            path = "/index.html"

        file_path = STATIC_DIR / path.lstrip("/")
        if file_path.exists() and file_path.is_file():
            content = file_path.read_bytes()
            content_type = self._guess_type(file_path.suffix)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
            return

        self.send_response(404)
        self.end_headers()

    def _guess_type(self, suffix):
        return {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(suffix, "application/octet-stream")

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass


def create_server(host: str, port: int, hermes_dir: Path) -> HTTPServer:
    """Create and return the dashboard HTTP server."""
    global HERMES_DIR
    HERMES_DIR = hermes_dir
    return HTTPServer((host, port), DashboardHandler)
