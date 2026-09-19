# db_helper.py | Python 3.10+ | supabase-py 2.x
# Supabase-backed storage for selfbot.py
# Replaces: config.json, HOSTED_TOKENS list, and per-user data in /data/*.json
#
# Schema (run once in Supabase SQL editor):
# ─────────────────────────────────────────────────────────────────────────────
# CREATE TABLE IF NOT EXISTS selfbot_config (
#     key   TEXT PRIMARY KEY,
#     value JSONB NOT NULL,
#     updated_at TIMESTAMPTZ DEFAULT now()
# );
#
# CREATE TABLE IF NOT EXISTS hosted_tokens (
#     id         BIGSERIAL PRIMARY KEY,
#     token      TEXT UNIQUE NOT NULL,
#     username   TEXT,
#     user_id    TEXT,
#     added_at   TIMESTAMPTZ DEFAULT now(),
#     active     BOOLEAN DEFAULT TRUE
# );
#
# CREATE TABLE IF NOT EXISTS sessions (
#     session_token TEXT PRIMARY KEY,
#     discord_token TEXT NOT NULL,
#     user_id       TEXT NOT NULL,
#     username      TEXT,
#     global_name   TEXT,
#     avatar        TEXT,
#     state         JSONB DEFAULT '{}'::jsonb,
#     connected_at  TIMESTAMPTZ DEFAULT now(),
#     last_seen     TIMESTAMPTZ DEFAULT now()
# );
# ─────────────────────────────────────────────────────────────────────────────

import os
import json
import time
import asyncio
import traceback
from typing import Any, Optional

try:
    from supabase import create_client, Client as SupabaseClient
    _HAS_SUPABASE = True
except ImportError:
    _HAS_SUPABASE = False
    print("[db_helper] supabase-py not installed — run: pip install supabase")

# ── connection ────────────────────────────────────────────────────────────────
# ⚠️  HARDCODED CREDENTIALS — DO NOT COMMIT / SHARE THIS FILE.
# The key below is a service_role / secret key with FULL database access.
# Anyone who sees this file can read, modify, and delete your entire database.

SUPABASE_URL = "https://wbmgtspqqrhzodezcsmo.supabase.co"
SUPABASE_KEY = "sb_secret_BmNNH9JOl7P12jbSP-W3vQ_MiHcNDug"

# Env vars still override if you ever set them (optional).
SUPABASE_URL = os.environ.get("SUPABASE_URL", SUPABASE_URL).strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", SUPABASE_KEY).strip()

_client: Optional["SupabaseClient"] = None

def get_client() -> Optional["SupabaseClient"]:
    global _client
    if not _HAS_SUPABASE:
        return None
    if _client is None:
        if (not SUPABASE_URL) or (not SUPABASE_KEY) or ("PASTE_YOUR" in SUPABASE_KEY):
            print("[db_helper] Supabase URL/key missing or not filled in — falling back to local config")
            return None
        try:
            _client = create_client(SUPABASE_URL, SUPABASE_KEY)
            print(f"[db_helper] ✓ connected to Supabase ({SUPABASE_URL[:40]}...)")
        except Exception as e:
            print(f"[db_helper] connection failed: {e}")
            _client = None
    return _client

# ── local fallback path ───────────────────────────────────────────────────────
# If Supabase is unreachable we transparently fall back to config.json
# so the bot never hard-crashes on a DB outage.

_LOCAL_PATH = "config.json"

def _local_load() -> dict:
    if os.path.exists(_LOCAL_PATH):
        try:
            with open(_LOCAL_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _local_save(data: dict):
    try:
        with open(_LOCAL_PATH, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[db_helper] local save error: {e}")

# ── config helpers ────────────────────────────────────────────────────────────

def config_get(key: str, default: Any = None) -> Any:
    """Read a config value. Tries Supabase first, falls back to local."""
    db = get_client()
    if db:
        try:
            res = db.table("selfbot_config").select("value").eq("key", key).execute()
            if res.data:
                return res.data[0]["value"]
        except Exception as e:
            print(f"[db_helper] config_get({key}) supabase error: {e}")
    # fallback
    return _local_load().get(key, default)


def config_set(key: str, value: Any):
    """Write a config value. Writes to Supabase AND local file."""
    # always write local so there's always a backup
    local = _local_load()
    local[key] = value
    _local_save(local)

    db = get_client()
    if db:
        try:
            db.table("selfbot_config").upsert({
                "key": key,
                "value": value,
                "updated_at": "now()",
            }).execute()
        except Exception as e:
            print(f"[db_helper] config_set({key}) supabase error: {e}")


def config_get_all() -> dict:
    """Return full config as a flat dict."""
    db = get_client()
    base = _local_load()
    if db:
        try:
            res = db.table("selfbot_config").select("key,value").execute()
            for row in res.data:
                base[row["key"]] = row["value"]
        except Exception as e:
            print(f"[db_helper] config_get_all supabase error: {e}")
    return base


def config_delete(key: str):
    local = _local_load()
    local.pop(key, None)
    _local_save(local)
    db = get_client()
    if db:
        try:
            db.table("selfbot_config").delete().eq("key", key).execute()
        except Exception as e:
            print(f"[db_helper] config_delete({key}) supabase error: {e}")

# ── hosted token helpers ──────────────────────────────────────────────────────

def hosted_tokens_get() -> list[str]:
    """Return list of active hosted tokens."""
    db = get_client()
    if db:
        try:
            res = db.table("hosted_tokens").select("token").eq("active", True).execute()
            return [row["token"] for row in res.data]
        except Exception as e:
            print(f"[db_helper] hosted_tokens_get supabase error: {e}")
    # fallback
    return _local_load().get("hosted_tokens", [])


def hosted_token_add(token: str, username: str = "", user_id: str = "") -> bool:
    """Add a token to hosted list. Returns True on success."""
    # local fallback
    local = _local_load()
    tokens = local.get("hosted_tokens", [])
    if token not in tokens:
        tokens.append(token)
        local["hosted_tokens"] = tokens
        _local_save(local)

    db = get_client()
    if db:
        try:
            db.table("hosted_tokens").upsert({
                "token": token,
                "username": username,
                "user_id": user_id,
                "active": True,
            }).execute()
            return True
        except Exception as e:
            print(f"[db_helper] hosted_token_add supabase error: {e}")
    return True  # local succeeded


def hosted_token_remove(token: str) -> bool:
    """Remove / deactivate a hosted token."""
    local = _local_load()
    tokens = local.get("hosted_tokens", [])
    if token in tokens:
        tokens.remove(token)
        local["hosted_tokens"] = tokens
        _local_save(local)

    db = get_client()
    if db:
        try:
            db.table("hosted_tokens").update({"active": False}).eq("token", token).execute()
            return True
        except Exception as e:
            print(f"[db_helper] hosted_token_remove supabase error: {e}")
    return True


def hosted_token_update_username(token: str, username: str, user_id: str = ""):
    db = get_client()
    if db:
        try:
            db.table("hosted_tokens").update({"username": username, "user_id": user_id}).eq("token", token).execute()
        except Exception:
            pass

# ── dashboard session helpers (used by server.js via IPC or direct Supabase) ─

def session_upsert(session_token: str, discord_token: str, user: dict, state: dict = None):
    db = get_client()
    if not db:
        return
    try:
        db.table("sessions").upsert({
            "session_token": session_token,
            "discord_token": discord_token,
            "user_id": user.get("id", ""),
            "username": user.get("username", ""),
            "global_name": user.get("global_name", ""),
            "avatar": user.get("avatar", ""),
            "state": state or {},
            "last_seen": "now()",
        }).execute()
    except Exception as e:
        print(f"[db_helper] session_upsert error: {e}")


def session_get(session_token: str) -> Optional[dict]:
    db = get_client()
    if not db:
        return None
    try:
        res = db.table("sessions").select("*").eq("session_token", session_token).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db_helper] session_get error: {e}")
        return None


def session_delete(session_token: str):
    db = get_client()
    if not db:
        return
    try:
        db.table("sessions").delete().eq("session_token", session_token).execute()
    except Exception as e:
        print(f"[db_helper] session_delete error: {e}")


def session_list() -> list[dict]:
    db = get_client()
    if not db:
        return []
    try:
        res = db.table("sessions").select("session_token,user_id,username,global_name,avatar,connected_at,last_seen").execute()
        return res.data
    except Exception as e:
        print(f"[db_helper] session_list error: {e}")
        return []


def session_update_state(session_token: str, state: dict):
    db = get_client()
    if not db:
        return
    try:
        db.table("sessions").update({"state": state, "last_seen": "now()"}).eq("session_token", session_token).execute()
    except Exception as e:
        print(f"[db_helper] session_update_state error: {e}")

# ── async wrappers (for use inside discord.py event loop) ────────────────────

async def async_config_get(key: str, default: Any = None) -> Any:
    return await asyncio.get_event_loop().run_in_executor(None, config_get, key, default)

async def async_config_set(key: str, value: Any):
    await asyncio.get_event_loop().run_in_executor(None, config_set, key, value)

async def async_hosted_tokens_get() -> list[str]:
    return await asyncio.get_event_loop().run_in_executor(None, hosted_tokens_get)

async def async_hosted_token_add(token: str, username: str = "", user_id: str = "") -> bool:
    return await asyncio.get_event_loop().run_in_executor(None, hosted_token_add, token, username, user_id)

async def async_hosted_token_remove(token: str) -> bool:
    return await asyncio.get_event_loop().run_in_executor(None, hosted_token_remove, token)

# ── bootstrap: sync local config → Supabase on first run ─────────────────────

def sync_local_to_supabase():
    """
    Call once on startup to push existing config.json values into Supabase.
    Safe to re-run — uses upsert so nothing is overwritten with blanks.
    """
    db = get_client()
    if not db:
        return
    local = _local_load()
    if not local:
        return
    print("[db_helper] syncing local config → Supabase...")
    for key, value in local.items():
        if key == "hosted_tokens":
            continue  # handle separately
        try:
            db.table("selfbot_config").upsert({"key": key, "value": value}).execute()
        except Exception as e:
            print(f"[db_helper] sync {key}: {e}")

    # sync hosted tokens
    for token in local.get("hosted_tokens", []):
        try:
            db.table("hosted_tokens").upsert({"token": token, "active": True}).execute()
        except Exception as e:
            print(f"[db_helper] sync token: {e}")
    print("[db_helper] ✓ sync complete")

# ── health check ──────────────────────────────────────────────────────────────

def health_check() -> dict:
    db = get_client()
    if not db:
        return {"supabase": False, "local": os.path.exists(_LOCAL_PATH)}
    try:
        db.table("selfbot_config").select("key").limit(1).execute()
        return {"supabase": True, "url": SUPABASE_URL[:40], "local": os.path.exists(_LOCAL_PATH)}
    except Exception as e:
        return {"supabase": False, "error": str(e), "local": os.path.exists(_LOCAL_PATH)}