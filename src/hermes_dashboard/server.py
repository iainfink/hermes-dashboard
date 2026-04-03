"""
Hermes Dashboard Server — Zero-dependency HTTP server + API.

Reads Hermes Agent state from SQLite DB and JSON config files.
No external dependencies beyond Python stdlib.
"""

import json
import os
import platform
import sqlite3
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Resolved at runtime via CLI
HERMES_DIR: Path = None
SERVER_START_TIME: float = None

STATIC_DIR = Path(__file__).parent / "static"

# Sensitive config keys to filter out
_SECRET_PATTERNS = (
    "key", "secret", "token", "password", "credential", "auth",
    "api_key", "apikey", "private", "signing",
)


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


def _read_gateway_state():
    """Read gateway_state.json safely."""
    gw = HERMES_DIR / "gateway_state.json"
    if gw.exists():
        try:
            return json.loads(gw.read_text())
        except Exception:
            pass
    return {}


def _pid_alive(pid):
    """Check if a process with given PID is alive."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _dir_size(path, max_files=1000):
    """Sum file sizes under a directory, capped at max_files."""
    total = 0
    count = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                if count >= max_files:
                    return total
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
                count += 1
    except OSError:
        pass
    return total


def _is_secret_key(key):
    """Check if a config key looks like it contains secrets."""
    lower = key.lower()
    return any(pat in lower for pat in _SECRET_PATTERNS)


def _filter_config(obj, depth=0):
    """Recursively filter secrets from config data."""
    if depth > 10:
        return "..."
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if _is_secret_key(str(k)):
                result[k] = "***REDACTED***"
            else:
                result[k] = _filter_config(v, depth + 1)
        return result
    elif isinstance(obj, list):
        return [_filter_config(i, depth + 1) for i in obj]
    else:
        # Check if value looks like a secret (long alphanumeric string)
        if isinstance(obj, str) and len(obj) > 20 and obj.startswith(("sk-", "pk-", "ghp_", "ghs_", "xoxb-", "xoxp-")):
            return "***REDACTED***"
        return obj


# ---------------------------------------------------------------------------
# API Handlers
# ---------------------------------------------------------------------------

def api_sessions(params):
    limit = int(params.get("limit", [30])[0])
    offset = int(params.get("offset", [0])[0])
    search = params.get("search", [None])[0]
    source = params.get("source", [None])[0]
    model = params.get("model", [None])[0]

    db = get_db()

    if search:
        # Full-text search across message content, return matching session IDs
        session_ids = db.execute(
            """SELECT DISTINCT session_id FROM messages
               WHERE content LIKE ? LIMIT 500""",
            (f"%{search}%",),
        ).fetchall()
        id_list = [r["session_id"] for r in session_ids]
        if not id_list:
            db.close()
            return []
        placeholders = ",".join("?" * len(id_list))
        query = f"""SELECT id, source, model, title, started_at, ended_at, end_reason,
                          message_count, tool_call_count, input_tokens, output_tokens,
                          cache_read_tokens, estimated_cost_usd, parent_session_id
                   FROM sessions WHERE id IN ({placeholders})
                   ORDER BY started_at DESC LIMIT ? OFFSET ?"""
        rows = db.execute(query, id_list + [limit, offset]).fetchall()
    else:
        conditions = []
        bind = []
        if source:
            conditions.append("source = ?")
            bind.append(source)
        if model:
            conditions.append("model = ?")
            bind.append(model)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"""SELECT id, source, model, title, started_at, ended_at, end_reason,
                          message_count, tool_call_count, input_tokens, output_tokens,
                          cache_read_tokens, estimated_cost_usd, parent_session_id
                   FROM sessions {where} ORDER BY started_at DESC LIMIT ? OFFSET ?"""
        bind.extend([limit, offset])
        rows = db.execute(query, bind).fetchall()

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


def api_session_detail(params):
    """Full session detail with all messages (untruncated), token counts, duration."""
    session_id = params.get("session_id", [None])[0]
    if not session_id:
        return {"error": "session_id required"}

    db = get_db()
    session = db.execute(
        """SELECT id, source, model, title, started_at, ended_at, end_reason,
                  message_count, tool_call_count, input_tokens, output_tokens,
                  cache_read_tokens, cache_write_tokens, reasoning_tokens,
                  estimated_cost_usd, parent_session_id, billing_provider
           FROM sessions WHERE id = ?""",
        (session_id,),
    ).fetchone()

    if not session:
        db.close()
        return {"error": "session not found"}

    messages = db.execute(
        """SELECT id, role, content, tool_call_id, tool_calls, tool_name,
                  timestamp, token_count, finish_reason
           FROM messages WHERE session_id = ? ORDER BY timestamp""",
        (session_id,),
    ).fetchall()

    # Child sessions (subagents)
    children = db.execute(
        """SELECT id, model, title, started_at, ended_at, message_count
           FROM sessions WHERE parent_session_id = ?
           ORDER BY started_at""",
        (session_id,),
    ).fetchall()

    db.close()

    duration = None
    if session["started_at"] and session["ended_at"]:
        duration = round(session["ended_at"] - session["started_at"], 2)

    return {
        "session": {
            "id": session["id"],
            "source": session["source"],
            "model": session["model"],
            "title": session["title"],
            "started_at": _ts(session["started_at"]),
            "ended_at": _ts(session["ended_at"]),
            "end_reason": session["end_reason"],
            "duration_seconds": duration,
            "message_count": session["message_count"],
            "tool_call_count": session["tool_call_count"],
            "input_tokens": session["input_tokens"],
            "output_tokens": session["output_tokens"],
            "cache_read_tokens": session["cache_read_tokens"],
            "cache_write_tokens": session["cache_write_tokens"],
            "reasoning_tokens": session["reasoning_tokens"],
            "estimated_cost_usd": session["estimated_cost_usd"],
            "parent_session_id": session["parent_session_id"],
            "billing_provider": session["billing_provider"],
            "is_subagent": session["parent_session_id"] is not None,
        },
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"] or "",
                "tool_call_id": m["tool_call_id"],
                "tool_calls": m["tool_calls"],
                "tool_name": m["tool_name"],
                "timestamp": _ts(m["timestamp"]),
                "token_count": m["token_count"],
                "finish_reason": m["finish_reason"],
            }
            for m in messages
        ],
        "children": [
            {
                "id": c["id"],
                "model": c["model"],
                "title": c["title"],
                "started_at": _ts(c["started_at"]),
                "ended_at": _ts(c["ended_at"]),
                "message_count": c["message_count"],
            }
            for c in children
        ],
    }


def api_gateway(_params):
    gw_data = _read_gateway_state()
    if gw_data:
        return gw_data
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


def api_skills_detail(params):
    """Return full content of a specific SKILL.md file."""
    name = params.get("name", [None])[0]
    if not name:
        return {"error": "name parameter required"}

    skills_dir = HERMES_DIR / "skills"
    if not skills_dir.exists():
        return {"error": "skills directory not found"}

    # Search for matching SKILL.md
    for item in skills_dir.rglob("SKILL.md"):
        rel = item.relative_to(skills_dir)
        parts = rel.parts
        skill_name = parts[-2] if len(parts) > 1 else parts[0]
        if skill_name == name:
            try:
                content = item.read_text()
                stat = item.stat()
                category = str(rel.parent.parent) if len(parts) > 2 else (
                    str(rel.parent) if len(parts) > 1 else ""
                )
                return {
                    "name": skill_name,
                    "category": category,
                    "path": str(rel),
                    "content": content,
                    "size_bytes": stat.st_size,
                    "modified_at": _ts(stat.st_mtime),
                }
            except Exception as e:
                return {"error": f"failed to read skill: {e}"}

    return {"error": f"skill '{name}' not found"}


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

    # DB file size
    db_path = HERMES_DIR / "state.db"
    db_size = 0
    try:
        db_size = os.path.getsize(db_path)
    except OSError:
        pass

    # HERMES_DIR total size (capped at 1000 files)
    hermes_dir_size = _dir_size(HERMES_DIR, max_files=1000)

    # Gateway uptime
    uptime_seconds = None
    gw = _read_gateway_state()
    if gw.get("updated_at"):
        try:
            uptime_seconds = round(time.time() - float(gw["updated_at"]), 1)
        except (TypeError, ValueError):
            pass
    elif gw.get("started_at"):
        try:
            uptime_seconds = round(time.time() - float(gw["started_at"]), 1)
        except (TypeError, ValueError):
            pass

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
        "db_size_bytes": db_size,
        "hermes_dir_size_bytes": hermes_dir_size,
        "uptime_seconds": uptime_seconds,
        "system": {
            "platform": platform.system(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "hostname": platform.node(),
        },
    }


def api_heartbeat(_params):
    """Health check with gateway and process status."""
    now = time.time()
    gw = _read_gateway_state()

    gateway_pid = gw.get("pid") or gw.get("gateway_pid")
    gateway_alive = _pid_alive(gateway_pid)

    # Scan for known Hermes processes
    hermes_processes = []
    # Check process registry
    registry = HERMES_DIR / "process_registry.json"
    if registry.exists():
        try:
            procs = json.loads(registry.read_text())
            if isinstance(procs, list):
                for p in procs:
                    pid = p.get("pid")
                    hermes_processes.append({
                        "pid": pid,
                        "name": p.get("name", "unknown"),
                        "alive": _pid_alive(pid),
                    })
        except Exception:
            pass

    dashboard_uptime = round(now - SERVER_START_TIME, 1) if SERVER_START_TIME else None

    return {
        "status": "ok",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "gateway_pid": gateway_pid,
        "gateway_alive": gateway_alive,
        "hermes_processes": hermes_processes,
        "dashboard_uptime_seconds": dashboard_uptime,
        "hermes_dir": str(HERMES_DIR),
    }


def api_processes(_params):
    registry = HERMES_DIR / "process_registry.json"
    if registry.exists():
        try:
            return json.loads(registry.read_text())
        except Exception:
            pass
    return []


def api_agents(_params):
    """List active agent sessions (ended_at IS NULL) and gateway info."""
    db = get_db()
    rows = db.execute(
        """SELECT id, source, model, title, started_at, message_count,
                  tool_call_count, input_tokens, output_tokens, parent_session_id
           FROM sessions WHERE ended_at IS NULL
           ORDER BY started_at DESC"""
    ).fetchall()
    db.close()

    agents = [
        {
            "session_id": r["id"],
            "source": r["source"],
            "model": r["model"],
            "title": r["title"],
            "started_at": _ts(r["started_at"]),
            "message_count": r["message_count"],
            "tool_call_count": r["tool_call_count"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "is_subagent": r["parent_session_id"] is not None,
        }
        for r in rows
    ]

    # Gateway info
    gw = _read_gateway_state()
    gateway_pid = gw.get("pid") or gw.get("gateway_pid")

    return {
        "active_agents": agents,
        "count": len(agents),
        "gateway": {
            "pid": gateway_pid,
            "alive": _pid_alive(gateway_pid),
            "state": gw,
        },
    }


def api_timeline(_params):
    """Return last 24h of activity grouped by hour for charting."""
    now = time.time()
    t24h_ago = now - 86400  # 24 hours

    db = get_db()

    # Sessions started per hour
    sessions_started = db.execute(
        """SELECT
            CAST((started_at - ?) / 3600 AS INTEGER) as hour_bucket,
            count(*) as count
           FROM sessions
           WHERE started_at >= ?
           GROUP BY hour_bucket
           ORDER BY hour_bucket""",
        (t24h_ago, t24h_ago),
    ).fetchall()

    # Sessions ended per hour
    sessions_ended = db.execute(
        """SELECT
            CAST((ended_at - ?) / 3600 AS INTEGER) as hour_bucket,
            count(*) as count
           FROM sessions
           WHERE ended_at >= ? AND ended_at IS NOT NULL
           GROUP BY hour_bucket
           ORDER BY hour_bucket""",
        (t24h_ago, t24h_ago),
    ).fetchall()

    # Messages per hour
    messages_sent = db.execute(
        """SELECT
            CAST((timestamp - ?) / 3600 AS INTEGER) as hour_bucket,
            count(*) as count
           FROM messages
           WHERE timestamp >= ?
           GROUP BY hour_bucket
           ORDER BY hour_bucket""",
        (t24h_ago, t24h_ago),
    ).fetchall()

    db.close()

    # Build 24-hour timeline
    timeline = []
    for h in range(25):
        hour_start = t24h_ago + h * 3600
        ts_iso = datetime.fromtimestamp(hour_start, tz=timezone.utc).isoformat()
        started = next((r["count"] for r in sessions_started if r["hour_bucket"] == h), 0)
        ended = next((r["count"] for r in sessions_ended if r["hour_bucket"] == h), 0)
        msgs = next((r["count"] for r in messages_sent if r["hour_bucket"] == h), 0)
        timeline.append({
            "hour": ts_iso,
            "hour_index": h,
            "sessions_started": started,
            "sessions_ended": ended,
            "messages": msgs,
        })

    return {
        "period_start": datetime.fromtimestamp(t24h_ago, tz=timezone.utc).isoformat(),
        "period_end": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "hours": timeline,
    }


def api_models(_params):
    """Return distinct models used with aggregated stats."""
    db = get_db()
    rows = db.execute(
        """SELECT
            model,
            count(*) as session_count,
            coalesce(sum(input_tokens), 0) as total_input_tokens,
            coalesce(sum(output_tokens), 0) as total_output_tokens,
            coalesce(sum(cache_read_tokens), 0) as total_cache_read_tokens,
            coalesce(sum(reasoning_tokens), 0) as total_reasoning_tokens,
            coalesce(sum(estimated_cost_usd), 0) as total_cost_usd,
            coalesce(sum(message_count), 0) as total_messages,
            coalesce(sum(tool_call_count), 0) as total_tool_calls,
            min(started_at) as first_used,
            max(started_at) as last_used
           FROM sessions
           WHERE model IS NOT NULL
           GROUP BY model
           ORDER BY session_count DESC"""
    ).fetchall()
    db.close()

    return [
        {
            "model": r["model"],
            "session_count": r["session_count"],
            "total_input_tokens": r["total_input_tokens"],
            "total_output_tokens": r["total_output_tokens"],
            "total_cache_read_tokens": r["total_cache_read_tokens"],
            "total_reasoning_tokens": r["total_reasoning_tokens"],
            "total_cost_usd": round(r["total_cost_usd"], 4),
            "total_messages": r["total_messages"],
            "total_tool_calls": r["total_tool_calls"],
            "first_used": _ts(r["first_used"]),
            "last_used": _ts(r["last_used"]),
        }
        for r in rows
    ]


def api_config(_params):
    """Return safe subset of config.yaml (secrets redacted)."""
    config_path = HERMES_DIR / "config.yaml"
    if not config_path.exists():
        return {"error": "config.yaml not found", "path": str(config_path)}

    try:
        content = config_path.read_text()
    except Exception as e:
        return {"error": f"failed to read config: {e}"}

    # Try to parse YAML manually (no pyyaml dependency)
    # Simple approach: parse as structured lines, filter secrets
    # For a more robust approach, try importing yaml if available
    try:
        import yaml
        data = yaml.safe_load(content)
        return {"config": _filter_config(data), "format": "parsed"}
    except ImportError:
        pass

    # Fallback: line-by-line filtering
    filtered_lines = []
    skip_block = False
    for line in content.split("\n"):
        stripped = line.strip()
        # Skip comment-only lines
        if stripped.startswith("#"):
            filtered_lines.append(line)
            continue
        # Check if line has a key
        if ":" in stripped:
            key = stripped.split(":")[0].strip().strip("-").strip()
            if _is_secret_key(key):
                indent = len(line) - len(line.lstrip())
                filtered_lines.append(f"{' ' * indent}{key}: ***REDACTED***")
                skip_block = True
                continue
        if skip_block:
            # Check indentation to see if we're still in the secret block
            if stripped and not stripped.startswith("-") and ":" in stripped:
                skip_block = False
            else:
                continue
        skip_block = False
        filtered_lines.append(line)

    return {"config_text": "\n".join(filtered_lines), "format": "filtered_text"}


def api_chat_post(body):
    """Proxy chat request to Hermes API server at localhost:8642."""
    if not body:
        return {"error": "request body required"}

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"error": "invalid JSON body"}

    message = data.get("message")
    if not message:
        return {"error": "message field required"}

    session_id = data.get("session_id")
    model = data.get("model", "default")

    # Build OpenAI-compatible request
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": message}],
    }
    if session_id:
        payload["metadata"] = {"session_id": session_id}

    payload_bytes = json.dumps(payload).encode("utf-8")

    try:
        req = urllib.request.Request(
            "http://localhost:8642/v1/chat/completions",
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
            return response_data
    except urllib.error.URLError as e:
        return {
            "error": "Hermes API server not available",
            "detail": str(e),
            "instructions": (
                "The Hermes API server is not running at localhost:8642. "
                "To enable it, ensure the Hermes gateway is running with API mode enabled. "
                "Check your Hermes config.yaml for api_server settings, or start the "
                "gateway with: hermes gateway start --api-port 8642"
            ),
        }
    except Exception as e:
        return {"error": f"chat proxy failed: {e}"}


# ---------------------------------------------------------------------------
# Route table
# ---------------------------------------------------------------------------

ROUTES = {
    "/api/sessions": api_sessions,
    "/api/session/messages": api_session_messages,
    "/api/session/detail": api_session_detail,
    "/api/gateway": api_gateway,
    "/api/cron": api_cron_jobs,
    "/api/skills": api_skills,
    "/api/skills/detail": api_skills_detail,
    "/api/stats": api_stats,
    "/api/heartbeat": api_heartbeat,
    "/api/processes": api_processes,
    "/api/agents": api_agents,
    "/api/timeline": api_timeline,
    "/api/models": api_models,
    "/api/config": api_config,
}

POST_ROUTES = {
    "/api/chat": api_chat_post,
}


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the dashboard UI and API endpoints."""

    def _send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # API routes
        if path in ROUTES:
            try:
                data = ROUTES[path](params)
                self._send_json(data)
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
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

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in POST_ROUTES:
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length > 0 else b""
                data = POST_ROUTES[path](body.decode("utf-8") if body else "")
                status = 200
                if isinstance(data, dict) and "error" in data:
                    status = 502 if "not available" in data.get("error", "") else 400
                self._send_json(data, status=status)
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
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
            ".woff2": "font/woff2",
            ".woff": "font/woff",
            ".ttf": "font/ttf",
            ".map": "application/json",
        }.get(suffix, "application/octet-stream")

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass


def create_server(host: str, port: int, hermes_dir: Path) -> HTTPServer:
    """Create and return the dashboard HTTP server."""
    global HERMES_DIR, SERVER_START_TIME
    HERMES_DIR = hermes_dir
    SERVER_START_TIME = time.time()
    return HTTPServer((host, port), DashboardHandler)
