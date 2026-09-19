# check_db.py — quick Supabase health check
from db_helper import get_client, health_check, config_get_all, hosted_tokens_get, session_list

print("\n=== HEALTH ===")
print(health_check())

db = get_client()
if not db:
    print("\n❌ get_client() returned None — Supabase not connected.")
    print("   Check that SUPABASE_KEY is filled in inside db_helper.py.")
    raise SystemExit(1)

print("\n=== ROW COUNTS ===")
for table in ("selfbot_config", "hosted_tokens", "sessions"):
    try:
        res = db.table(table).select("*", count="exact").execute()
        print(f"{table:20s} → {len(res.data)} rows")
    except Exception as e:
        print(f"{table:20s} → ERROR: {e}")

print("\n=== selfbot_config CONTENTS ===")
cfg = config_get_all()
if not cfg:
    print("(empty — nothing in selfbot_config yet)")
else:
    for k, v in cfg.items():
        print(f"  {k} = {str(v)[:80]}")

print("\n=== hosted_tokens CONTENTS ===")
toks = hosted_tokens_get()
if not toks:
    print("(empty — no tokens hosted yet)")
else:
    for t in toks:
        print(f"  {t[:12]}...")

print("\n=== sessions CONTENTS ===")
sess = session_list()
if not sess:
    print("(empty — no dashboard sessions yet)")
else:
    for s in sess:
        print(f"  {s.get('username')} ({s.get('user_id')}) last_seen={s.get('last_seen')}")

print()