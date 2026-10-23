#!/usr/bin/env python3
"""
LeadX — LinkedIn + Instagram connection test (standalone).

Usage:
  cd /workspaces/Leadx
  ./test_social.sh
  # or:
  PYTHONPATH=lead-engine-v2 python3 scripts/test_linkedin_instagram.py

Env (lead-engine-v2/.env):
  LINKEDIN_SCRAPER_URL=http://127.0.0.1:8001
  LINKEDIN_LI_AT_COOKIE=...   # full li_at, usually 100+ chars
  INSTAGRAM_USERNAME=...
  INSTAGRAM_PASSWORD=...
  INSTAGRAM_SESSION_FILE=/tmp/leadx_instagram_session

Flags:
  --clear-ig-session   delete session file before login
  --skip-linkedin
  --skip-instagram
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# ── paths / env ──────────────────────────────────────────────────────────────

def _find_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parent, here.parent.parent, Path.cwd(), Path.cwd().parent]:
        if (p / "lead-engine-v2").is_dir() or (p / "scripts" / "test_linkedin_instagram.py").exists():
            if (p / "lead-engine-v2").is_dir():
                return p
            if (p.parent / "lead-engine-v2").is_dir():
                return p.parent
    return Path.cwd()


ROOT = _find_root()
ENV_CANDIDATES = [
    ROOT / "lead-engine-v2" / ".env",
    ROOT / ".env",
    Path.cwd() / "lead-engine-v2" / ".env",
    Path.cwd() / ".env",
]


def load_env() -> Path | None:
    for ep in ENV_CANDIDATES:
        if ep.is_file():
            for line in ep.read_text(errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
            return ep
    return None


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def bad(msg: str) -> None:
    print(f"  ✗ {msg}")


def info(msg: str) -> None:
    print(f"  · {msg}")


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


# ── LinkedIn ─────────────────────────────────────────────────────────────────

def test_linkedin() -> bool:
    section("LINKEDIN SCRAPER")
    import urllib.request
    import urllib.error

    raw = (os.getenv("LINKEDIN_SCRAPER_URL") or "http://127.0.0.1:8001").strip().rstrip("/")
    if raw.startswith("https://"):
        host = raw[len("https://"):]
        if host.startswith("127.") or "localhost" in host or "linkedin-scraper" in host:
            fixed = "http://" + host
            info(f"URL was HTTPS — using {fixed} (scraper is HTTP-only)")
            raw = fixed
    print(f"URL: {raw}")

    cookie = (
        os.getenv("LINKEDIN_LI_AT_COOKIE")
        or os.getenv("LINKEDIN_LI_AT")
        or os.getenv("LI_AT")
        or ""
    ).strip()
    if not cookie:
        bad("LINKEDIN_LI_AT_COOKIE not set in .env")
        info("Copy li_at from browser → DevTools → Application → Cookies → linkedin.com")
        return False
    if len(cookie) < 40:
        bad(f"Cookie too short ({len(cookie)} chars) — need full li_at (usually 100+)")
        return False
    ok(f"LINKEDIN_LI_AT_COOKIE set ({len(cookie)} chars)")
    marker = Path("/tmp/leadx_linkedin_cookie_expired")
    if marker.exists():
        bad("EXPIRY MARKER present — cookie was rejected earlier")
        try:
            info(f"marker: {marker.read_text().strip()[:120]}")
        except Exception:
            pass
        info("Update LINKEDIN_LI_AT_COOKIE, then: rm -f /tmp/leadx_linkedin_cookie_expired")


    # Health
    try:
        req = urllib.request.Request(f"{raw}/health", method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body) if body.strip().startswith("{") else {}
            ok(f"GET /health → {resp.status} {body[:180]}")
            if data.get("browser_alive") is False:
                info("browser_alive=false is OK until first search (Chrome starts on demand)")
    except urllib.error.URLError as e:
        bad(f"Cannot reach scraper: {e}")
        info("Start it:")
        info("  cd lead-engine-v2 && docker compose --profile social up -d linkedin-scraper")
        info("  docker ps | grep linkedin")
        info("  curl -s http://127.0.0.1:8001/health")
        return False
    except Exception as e:
        bad(f"Health check failed: {e}")
        return False

    # company-search (starts Chrome)
    payload = json.dumps({"name": "Microsoft", "location": "United States"}).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Li-At": cookie,
        "Authorization": f"Bearer {cookie}",
    }
    try:
        req = urllib.request.Request(
            f"{raw}/company-search", data=payload, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ok(f"POST /company-search → {resp.status}")
            preview = body[:300].replace("\n", " ")
            info(f"Body preview: {preview}")
            if resp.status == 200:
                ok("LinkedIn deep scrape path works")
                return True
            bad(f"Unexpected status {resp.status}")
            return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        bad(f"POST /company-search → {e.code}")
        info(f"Body: {body[:400]}")
        if e.code == 500:
            info("Chrome likely crashed inside the container. Debug:")
            info("  docker logs lead-engine-v2-linkedin-scraper-1 2>&1 | tail -40")
            info("  docker update --memory=1g lead-engine-v2-linkedin-scraper-1")
            info("  docker restart lead-engine-v2-linkedin-scraper-1")
            info("Pipeline can still use Serper LinkedIn without this scraper.")
        if e.code in (401, 403):
            info("Cookie expired — log into LinkedIn in browser and copy a fresh li_at")
        return False
    except Exception as e:
        bad(f"company-search error: {e}")
        info("docker logs lead-engine-v2-linkedin-scraper-1 2>&1 | tail -40")
        return False


# ── Instagram ────────────────────────────────────────────────────────────────

def test_instagram(clear_session: bool) -> bool:
    section("INSTAGRAM (instaloader)")
    user = (os.getenv("INSTAGRAM_USERNAME") or "").strip()
    password = (os.getenv("INSTAGRAM_PASSWORD") or "").strip()
    session_file = (
        os.getenv("INSTAGRAM_SESSION_FILE") or "/tmp/leadx_instagram_session"
    ).strip()
    test_handle = (os.getenv("INSTAGRAM_TEST_HANDLE") or "instagram").strip().lstrip("@")

    print(f"INSTAGRAM_USERNAME: {user or '(not set)'}")
    print(f"INSTAGRAM_PASSWORD: {'set' if password else '(not set)'}")
    print(f"INSTAGRAM_SESSION_FILE: {session_file}")

    if clear_session:
        for path in (session_file, f"{session_file}.json", session_file + "-instagram"):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    ok(f"Removed session: {path}")
            except Exception as e:
                info(f"Could not remove {path}: {e}")

    try:
        import instaloader
    except ImportError:
        bad("instaloader not installed")
        info("  pip install instaloader")
        info("  # or: source lead-engine-v2/.venv/bin/activate && pip install instaloader")
        return False
    ok(f"instaloader import OK")

    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        quiet=True,
        max_connection_attempts=1,
    )
    try:
        L.context.max_connection_attempts = 1
    except Exception:
        pass

    logged_in = False
    if user:
        # try load session first
        loaded = False
        try:
            L.load_session_from_file(user, session_file)
            logged_in = True
            loaded = True
            ok(f"Session loaded for {user}")
        except Exception:
            info("No valid session file — will try password login")

        if not loaded and password:
            try:
                info("Logging in (may fail with checkpoint from Codespace IP)…")
                L.login(user, password)
                logged_in = True
                ok(f"Logged in as {user}")
                try:
                    L.save_session_to_file(session_file)
                    ok(f"Session saved → {session_file}")
                except Exception as e:
                    info(f"Session save failed: {e}")
            except Exception as e:
                msg = str(e)
                bad(f"Login failed: {msg[:400]}")
                if "checkpoint" in msg.lower() or "challenge" in msg.lower() or "auth_platform" in msg.lower():
                    info("Instagram wants a security check:")
                    info("  1. Open Instagram app/website on your phone")
                    info("  2. Approve 'This was me' if shown")
                    info("  3. Wait 10–30 minutes")
                    info("  4. ./test_social.sh --clear-ig-session")
                    info("  Do NOT spam the test while rate-limited.")
                if "429" in msg or "Too Many" in msg:
                    info("Rate limited — wait 30–60+ minutes, then --clear-ig-session once")
                if not password:
                    bad("INSTAGRAM_PASSWORD empty")
        elif not password and not loaded:
            bad("Need INSTAGRAM_PASSWORD or an existing session file")
    else:
        info("No INSTAGRAM_USERNAME — testing public profile only (hashtags need login)")

    # Profile fetch (works better logged-in; public sometimes works)
    try:
        info(f"Fetching profile @{test_handle}…")
        profile = instaloader.Profile.from_username(L.context, test_handle)
        ok(
            f"Profile @{profile.username} followers={profile.followers} "
            f"bio={(profile.biography or '')[:60]!r}"
        )
        profile_ok = True
    except Exception as e:
        msg = str(e)
        bad(f"Profile fetch failed: {msg[:300]}")
        profile_ok = False
        if "429" in msg or "Too Many" in msg:
            info("429 — stop testing for a while; do not retry in a loop")
        if "login_required" in msg.lower() or "403" in msg:
            info("Needs successful login/session for this endpoint")

    # Optional hashtag only if logged in
    tag_ok = False
    if logged_in:
        tag = (os.getenv("INSTAGRAM_TEST_TAG") or "medspa").strip().lstrip("#")
        try:
            info(f"Hashtag #{tag} (needs login)…")
            it = L.get_hashtag_posts(tag)
            first = next(it, None)
            if first is not None:
                ok(f"Hashtag #{tag} returned posts")
                tag_ok = True
            else:
                info(f"Hashtag #{tag} empty (not necessarily an error)")
                tag_ok = True  # login worked enough to query
        except Exception as e:
            msg = str(e)
            bad(f"Hashtag failed: {msg[:250]}")
            if "login_required" in msg.lower():
                info("Session not accepted for hashtags — complete checkpoint, clear session, retry once")
            if "429" in msg:
                info("Rate limited on hashtag — wait before retry")
    else:
        info("Skip hashtag test (not logged in)")

    success = profile_ok or (logged_in and tag_ok)
    if success:
        ok("Instagram connection usable for enrich (profile path)")
    else:
        bad("Instagram not ready — fix checkpoint/429 then re-run with --clear-ig-session")
    return success


def main() -> int:
    parser = argparse.ArgumentParser(description="LeadX LinkedIn + Instagram connection test")
    parser.add_argument("--clear-ig-session", action="store_true")
    parser.add_argument("--skip-linkedin", action="store_true")
    parser.add_argument("--skip-instagram", action="store_true")
    args = parser.parse_args()

    env_path = load_env()
    print("LeadX social connection test")
    print(f"Repo root: {ROOT}")
    print(f"Loaded env: {env_path or '(none found — using process env only)'}")

    results = {}
    if not args.skip_linkedin:
        results["linkedin"] = test_linkedin()
    if not args.skip_instagram:
        results["instagram"] = test_instagram(clear_session=args.clear_ig_session)

    section("SUMMARY")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")

    print()
    print("Debug cheatsheet:")
    print("  LinkedIn logs:  docker logs lead-engine-v2-linkedin-scraper-1 2>&1 | tail -50")
    print("  Restart LI:     docker restart lead-engine-v2-linkedin-scraper-1")
    print("  IG clear:       ./test_social.sh --clear-ig-session")
    print("  IG only:        ./test_social.sh --skip-linkedin")
    print("  LI only:        ./test_social.sh --skip-instagram")

    if not results:
        return 0
    if all(results.values()):
        return 0
    if any(results.values()):
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
