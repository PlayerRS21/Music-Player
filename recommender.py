#!/usr/bin/env python3
"""
recommender.py — Per-User Taste Engine
────────────────────────────────────────
Each user gets their own taste_<username>.db file.
Supports cold/warm/hot warmth system.

Cold  (0-9  plays): random popular seeds
Warm  (10-49 plays): genre-aware, channel-weighted
Hot   (50+  plays): fully personalized, replay-weighted
"""

import os, re, csv, sqlite3, argparse, random
from datetime import datetime
from html.parser import HTMLParser
from collections import defaultdict

SD = os.path.dirname(os.path.abspath(__file__))

# ── Cold start seed pool (diverse genres) ─────────────────────────────────────
COLD_SEEDS = [
    "dQw4w9WgXcQ","kJQP7kiw5Fk","9bZkp7q19f0","OPf0YbXqDm0",  # music
    "fLexgOxsZu0","YqeW9_5kURI","hT_nvWreIhg","RgKAFK5djSk",  # music
    "aircAruvnKk","WqiW-KbSX9s","rfscVS0vtbw","l9YxTXDiiFY",  # tech/edu
    "S7nh4EqFUMM","K2D3PgFGbhw","jNQXAC9IVRw","6JYIGclVQdw",  # gaming/comedy
    "BHACKCNDMW8","V-_O7nl0Ii0","pRpeEdMmmQ0","M7lc1UVf-VE",  # nature/chill
]

# ── Active user (set by ytterm at startup) ────────────────────────────────────
_active_user = "default"

def set_user(username):
    global _active_user
    _active_user = username

def get_db_path(username=None):
    name = username or _active_user
    return os.path.join(SD, f"taste_{name}.db")

def db_exists(username=None):
    return os.path.exists(get_db_path(username))

# ── List all saved users ──────────────────────────────────────────────────────
def list_users():
    """Return list of usernames that have a taste DB."""
    users = []
    for f in os.listdir(SD):
        if f.startswith("taste_") and f.endswith(".db"):
            name = f[6:-3]  # strip taste_ and .db
            if name not in ("__anon__",):  # skip anonymous session DB
                users.append(name)
    return sorted(users)

# ── DB setup ──────────────────────────────────────────────────────────────────
def get_db(username=None):
    path = get_db_path(username)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS channel_scores (
            channel_id   TEXT PRIMARY KEY,
            channel_name TEXT,
            watch_count  INTEGER DEFAULT 0,
            skip_count   INTEGER DEFAULT 0,
            replay_count INTEGER DEFAULT 0,
            score        REAL    DEFAULT 0.0
        );
        CREATE TABLE IF NOT EXISTS keyword_scores (
            keyword TEXT PRIMARY KEY,
            score   REAL DEFAULT 0.0
        );
        CREATE TABLE IF NOT EXISTS video_scores (
            video_id     TEXT PRIMARY KEY,
            title        TEXT,
            channel      TEXT,
            play_count   INTEGER DEFAULT 0,
            skip_count   INTEGER DEFAULT 0,
            replay_count INTEGER DEFAULT 0,
            score        REAL    DEFAULT 0.0,
            last_played  TEXT
        );
        CREATE TABLE IF NOT EXISTS watch_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id   TEXT,
            title      TEXT,
            channel    TEXT,
            played_at  TEXT,
            completed  INTEGER DEFAULT 0,
            is_replay  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS playlist_signals (
            video_id      TEXT,
            playlist_name TEXT,
            PRIMARY KEY (video_id, playlist_name)
        );
        CREATE TABLE IF NOT EXISTS user_playlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT UNIQUE,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS user_playlist_items (
            playlist_id INTEGER,
            video_id    TEXT,
            title       TEXT,
            channel     TEXT,
            url         TEXT,
            added_at    TEXT,
            position    INTEGER DEFAULT 0,
            PRIMARY KEY (playlist_id, video_id),
            FOREIGN KEY (playlist_id) REFERENCES user_playlists(id)
        );
    """)
    return conn

def init_db():
    """Create DB for current user (called on first run)."""
    conn = get_db(); conn.close()

# ── Warmth ────────────────────────────────────────────────────────────────────
def get_warmth(username=None):
    if not db_exists(username): return "cold"
    conn  = get_db(username)
    plays = conn.execute(
        "SELECT COUNT(*) FROM watch_history WHERE completed=1"
    ).fetchone()[0]
    conn.close()
    if plays < 10: return "cold"
    if plays < 50: return "warm"
    return "hot"

def get_play_count(username=None):
    if not db_exists(username): return 0
    conn = get_db(username)
    n    = conn.execute("SELECT COUNT(*) FROM watch_history WHERE completed=1").fetchone()[0]
    conn.close()
    return n

# ── Stopwords ─────────────────────────────────────────────────────────────────
SW = {
    "a","an","the","is","in","on","at","to","for","of","and","or","but","with",
    "this","that","it","by","from","as","be","was","are","were","has","have",
    "had","i","you","he","she","we","they","not","no","so","do","did","will",
    "can","my","your","its","new","how","what","why","when","who","official",
    "video","full","ft","feat","lyrics","audio","hd","4k","2023","2024","2025",
    "2026","ep","live","mix","edit","version","re","vs","amp","out","up","get",
}
def _kw(title):
    return [w for w in re.findall(r"[a-zA-Z]{3,}", title.lower()) if w not in SW]

# ── HTML parser ───────────────────────────────────────────────────────────────
class WatchHistoryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries = []
        self._cur    = {}
        self._state  = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "a" and "href" in d:
            m = re.search(r"youtube\.com/watch\?v=([\w-]+)", d["href"])
            if m:
                self._cur   = {"video_id": m.group(1), "title":"", "channel":""}
                self._state = "title"

    def handle_data(self, data):
        data = data.strip()
        if not data: return
        if self._state == "title" and self._cur:
            self._cur["title"] = data; self._state = "channel"
        elif self._state == "channel" and self._cur:
            if data and not data.startswith("http"):
                self._cur["channel"] = data
                self.entries.append(self._cur)
                self._cur = {}; self._state = None

# ── Takeout importers ─────────────────────────────────────────────────────────
def import_watch_history(conn, html_path):
    print("  Parsing watch-history.html…")
    parser = WatchHistoryParser()
    parser.feed(open(html_path,"r",encoding="utf-8",errors="ignore").read())
    entries = parser.entries
    print(f"  Found {len(entries)} entries")
    ch_counts = defaultdict(lambda: {"name":"","count":0})
    kw_counts = defaultdict(float)
    for e in entries:
        ch = e["channel"]
        if ch:
            ch_counts[ch]["name"]   = ch
            ch_counts[ch]["count"] += 1
        for w in _kw(e["title"]): kw_counts[w] += 0.5
    c = conn.cursor()
    for ch_name, data in ch_counts.items():
        c.execute("""INSERT INTO channel_scores(channel_id,channel_name,watch_count,score)
            VALUES(?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET
            watch_count=watch_count+excluded.watch_count,score=score+excluded.score""",
            (ch_name.lower().replace(" ","_"), ch_name, data["count"], float(data["count"])))
    for kw, sc in kw_counts.items():
        c.execute("""INSERT INTO keyword_scores(keyword,score) VALUES(?,?)
            ON CONFLICT(keyword) DO UPDATE SET score=score+excluded.score""", (kw, sc))
    conn.commit()
    print(f"  ✓ {len(ch_counts)} channels, {len(kw_counts)} keywords")

def import_playlists(conn, pl_dir):
    print("  Parsing playlists…")
    c, total = conn.cursor(), 0
    for fname in os.listdir(pl_dir):
        if not fname.endswith(".csv"): continue
        pl_name = fname.replace(" videos.csv","").replace(".csv","")
        try:
            with open(os.path.join(pl_dir,fname),"r",encoding="utf-8",errors="ignore") as f:
                for row in csv.DictReader(f):
                    vid = (row.get("Video ID") or row.get("video_id","")).strip()
                    if not vid: continue
                    c.execute("INSERT OR IGNORE INTO playlist_signals(video_id,playlist_name) VALUES(?,?)",
                              (vid, pl_name))
                    total += 1
                    ch = (row.get("Channel Title") or "").strip()
                    if ch:
                        c.execute("""INSERT INTO channel_scores(channel_id,channel_name,score)
                            VALUES(?,?,2.0) ON CONFLICT(channel_id) DO UPDATE SET score=score+2.0""",
                            (ch.lower().replace(" ","_"), ch))
        except Exception as ex:
            print(f"    Warning {fname}: {ex}")
    conn.commit()
    print(f"  ✓ {total} playlist entries")

def import_music_library(conn, csv_path):
    print("  Parsing YT Music library…")
    c, total = conn.cursor(), 0
    try:
        with open(csv_path,"r",encoding="utf-8",errors="ignore") as f:
            for row in csv.DictReader(f):
                vid    = (row.get("YouTube Video ID") or "").strip()
                artist = (row.get("Artist") or "").strip()
                title  = (row.get("Title") or "").strip()
                if vid:
                    c.execute("INSERT OR IGNORE INTO playlist_signals(video_id,playlist_name) VALUES(?,'YT Music Library')", (vid,))
                    total += 1
                if artist:
                    c.execute("""INSERT INTO channel_scores(channel_id,channel_name,score)
                        VALUES(?,?,3.0) ON CONFLICT(channel_id) DO UPDATE SET score=score+3.0""",
                        (artist.lower().replace(" ","_"), artist))
                if title:
                    for w in _kw(title):
                        c.execute("""INSERT INTO keyword_scores(keyword,score) VALUES(?,1.5)
                            ON CONFLICT(keyword) DO UPDATE SET score=score+1.5""", (w,))
    except Exception as ex:
        print(f"    Warning: {ex}")
    conn.commit()
    print(f"  ✓ {total} songs")

def import_takeout(takeout_root, username=None):
    """
    Import Google Takeout data into a specific user's DB.
    username — if given, imports into that user's DB.
               if None, uses the currently active user (_active_user).
    """
    # Resolve target username
    target = username or _active_user

    # Find the YouTube folder inside Takeout
    yt_root = None
    for root, dirs, _ in os.walk(takeout_root):
        if "YouTube and YouTube Music" in root:
            yt_root = root; break
        for d in dirs:
            cand = os.path.join(root, d)
            if "YouTube" in d and os.path.isdir(cand):
                yt_root = cand; break
        if yt_root: break
    if not yt_root:
        cand = os.path.join(takeout_root, "YouTube and YouTube Music")
        yt_root = cand if os.path.exists(cand) else None
    if not yt_root:
        print(f"[ERROR] 'YouTube and YouTube Music' folder not found in:\n  {takeout_root}")
        return False

    print(f"\n  Importing into profile '{target}'")
    print(f"  Takeout path: {yt_root}\n")

    # Always import into the target user's DB specifically
    set_user(target)
    conn = get_db(target)   # explicitly pass username — no ambiguity

    wh = os.path.join(yt_root, "history", "watch-history.html")
    if os.path.exists(wh):
        import_watch_history(conn, wh)
    else:
        print("  (no watch-history.html found — skipping)")

    pl = os.path.join(yt_root, "playlists")
    if os.path.exists(pl):
        import_playlists(conn, pl)
    else:
        print("  (no playlists folder found — skipping)")

    ml = os.path.join(yt_root, "music (library and uploads)", "music library songs.csv")
    if os.path.exists(ml):
        import_music_library(conn, ml)
    else:
        print("  (no YT Music library found — skipping)")

    conn.close()
    db = get_db_path(target)
    print(f"\n  ✅  Done → {db}")
    return True

# ── User playlist management ──────────────────────────────────────────────────
def create_playlist(name):
    """Create a new user playlist. Returns playlist_id."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute("INSERT INTO user_playlists(name,created_at) VALUES(?,?)",
                  (name, datetime.now().isoformat()))
        conn.commit()
        pl_id = c.lastrowid
        conn.close()
        return pl_id
    except sqlite3.IntegrityError:
        row = c.execute("SELECT id FROM user_playlists WHERE name=?", (name,)).fetchone()
        conn.close()
        return row["id"] if row else None

def get_playlists():
    """Return list of {id, name, count} for all user playlists."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute("""
        SELECT p.id, p.name,
               COUNT(i.video_id) as count
        FROM user_playlists p
        LEFT JOIN user_playlist_items i ON i.playlist_id=p.id
        GROUP BY p.id ORDER BY p.name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_to_playlist(pl_id, video):
    """Add a video dict to a playlist."""
    conn = get_db(); c = conn.cursor()
    row = c.execute("SELECT MAX(position) as mp FROM user_playlist_items WHERE playlist_id=?",
                    (pl_id,)).fetchone()
    pos = (row["mp"] or 0) + 1
    try:
        c.execute("""INSERT OR IGNORE INTO user_playlist_items
            (playlist_id,video_id,title,channel,url,added_at,position)
            VALUES(?,?,?,?,?,?,?)""",
            (pl_id, video["id"], video["title"], video["channel"],
             video.get("url", f"https://www.youtube.com/watch?v={video['id']}"),
             datetime.now().isoformat(), pos))
        conn.commit()
    except Exception as ex:
        print(f"    Warning: {ex}")
    conn.close()

def get_playlist_videos(pl_id):
    """Return ordered list of video dicts for a playlist."""
    conn = get_db(); c = conn.cursor()
    rows = c.execute("""SELECT video_id,title,channel,url FROM user_playlist_items
        WHERE playlist_id=? ORDER BY position""", (pl_id,)).fetchall()
    conn.close()
    return [{"id": r["video_id"], "title": r["title"],
             "channel": r["channel"], "url": r["url"]} for r in rows]

def remove_from_playlist(pl_id, video_id):
    conn = get_db()
    conn.execute("DELETE FROM user_playlist_items WHERE playlist_id=? AND video_id=?",
                 (pl_id, video_id))
    conn.commit(); conn.close()

def delete_playlist(pl_id):
    conn = get_db()
    conn.execute("DELETE FROM user_playlist_items WHERE playlist_id=?", (pl_id,))
    conn.execute("DELETE FROM user_playlists WHERE id=?", (pl_id,))
    conn.commit(); conn.close()

# ── Scoring ───────────────────────────────────────────────────────────────────
def score_video(video, allow_seen=True):
    conn  = get_db(); c = conn.cursor(); score = 0.0
    vid   = video.get("id","")
    row   = c.execute("SELECT score,play_count FROM video_scores WHERE video_id=?", (vid,)).fetchone()
    if row:
        if not allow_seen and row["play_count"] > 0:
            conn.close(); return -999.0
        score += row["score"]
    ch_key = video.get("channel","").lower().replace(" ","_")
    row    = c.execute("SELECT score FROM channel_scores WHERE channel_id=?", (ch_key,)).fetchone()
    if row: score += row["score"] * 0.4
    for w in _kw(video.get("title","")):
        row = c.execute("SELECT score FROM keyword_scores WHERE keyword=?", (w,)).fetchone()
        if row: score += row["score"] * 0.08
    row = c.execute("SELECT COUNT(*) as n FROM playlist_signals WHERE video_id=?", (vid,)).fetchone()
    if row and row["n"] > 0: score += 20.0
    conn.close()
    return score

def rank_videos(videos, allow_seen=True):
    return sorted(videos, key=lambda v: score_video(v, allow_seen=allow_seen), reverse=True)

def get_seed_videos(n=6):
    """Return seed video IDs based on warmth level."""
    warmth = get_warmth()
    if warmth == "cold":
        seeds = COLD_SEEDS.copy(); random.shuffle(seeds)
        return [], seeds[:n]
    conn = get_db(); c = conn.cursor()
    if warmth == "warm":
        pl = [r["video_id"] for r in c.execute(
            "SELECT video_id FROM playlist_signals ORDER BY RANDOM() LIMIT ?", (n,)).fetchall()]
        ch = [r["channel_name"] for r in c.execute(
            "SELECT channel_name FROM channel_scores ORDER BY score DESC LIMIT ?", (n//2,)).fetchall()]
        conn.close(); return ch, pl
    rp = [r["video_id"] for r in c.execute(
        "SELECT video_id FROM video_scores WHERE replay_count>0 ORDER BY score DESC LIMIT ?",
        (n,)).fetchall()]
    pl = [r["video_id"] for r in c.execute(
        "SELECT video_id FROM playlist_signals ORDER BY RANDOM() LIMIT ?", (n//2,)).fetchall()]
    ch = [r["channel_name"] for r in c.execute(
        "SELECT channel_name FROM channel_scores ORDER BY score DESC LIMIT ?", (n//2,)).fetchall()]
    conn.close()
    return ch, rp + pl

# ── Logging ───────────────────────────────────────────────────────────────────
def _was_played(c, vid):
    row = c.execute("SELECT play_count FROM video_scores WHERE video_id=?", (vid,)).fetchone()
    return row is not None and row["play_count"] > 0

def log_play(video, completed=True):
    conn   = get_db(); c = conn.cursor()
    vid    = video.get("id",""); ch = video.get("channel","")
    ch_key = ch.lower().replace(" ","_")
    is_rep = _was_played(c, vid)
    vd     = 5.0 if is_rep else 1.0
    cd     = 2.0 if is_rep else 1.0
    c.execute("""INSERT INTO video_scores(video_id,title,channel,play_count,replay_count,score,last_played)
        VALUES(?,?,?,1,?,?,?) ON CONFLICT(video_id) DO UPDATE SET
        play_count=play_count+1,replay_count=replay_count+?,score=score+?,last_played=excluded.last_played""",
        (vid,video.get("title",""),ch,1 if is_rep else 0,vd,datetime.now().isoformat(),1 if is_rep else 0,vd))
    c.execute("""INSERT INTO channel_scores(channel_id,channel_name,watch_count,replay_count,score)
        VALUES(?,?,1,?,?) ON CONFLICT(channel_id) DO UPDATE SET
        watch_count=watch_count+1,replay_count=replay_count+?,score=score+?""",
        (ch_key,ch,1 if is_rep else 0,cd,1 if is_rep else 0,cd))
    kd = 0.3 if is_rep else 0.15
    for w in _kw(video.get("title","")):
        c.execute("""INSERT INTO keyword_scores(keyword,score) VALUES(?,?)
            ON CONFLICT(keyword) DO UPDATE SET score=score+excluded.score""", (w, kd))
    c.execute("""INSERT INTO watch_history(video_id,title,channel,played_at,completed,is_replay)
        VALUES(?,?,?,?,1,?)""",
        (vid,video.get("title",""),ch,datetime.now().isoformat(),1 if is_rep else 0))
    conn.commit(); conn.close()

def log_skip(video):
    conn   = get_db(); c = conn.cursor()
    vid    = video.get("id",""); ch = video.get("channel","")
    ch_key = ch.lower().replace(" ","_")
    c.execute("""INSERT INTO video_scores(video_id,title,channel,skip_count,score,last_played)
        VALUES(?,?,?,1,-2.0,?) ON CONFLICT(video_id) DO UPDATE SET
        skip_count=skip_count+1,score=score-2.0,last_played=excluded.last_played""",
        (vid,video.get("title",""),ch,datetime.now().isoformat()))
    c.execute("UPDATE channel_scores SET skip_count=skip_count+1,score=MAX(0,score-0.3) WHERE channel_id=?",
              (ch_key,))
    c.execute("INSERT INTO watch_history(video_id,title,channel,played_at,completed) VALUES(?,?,?,?,0)",
              (vid,video.get("title",""),ch,datetime.now().isoformat()))
    conn.commit(); conn.close()

# ── Stats ─────────────────────────────────────────────────────────────────────
def get_stats():
    if not db_exists(): return None
    conn = get_db(); c = conn.cursor()
    stats = {
        "user":           _active_user,
        "warmth":         get_warmth(),
        "plays":          get_play_count(),
        "channels":       c.execute("SELECT COUNT(*) FROM channel_scores").fetchone()[0],
        "keywords":       c.execute("SELECT COUNT(*) FROM keyword_scores").fetchone()[0],
        "history":        c.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0],
        "replays":        c.execute("SELECT COUNT(*) FROM watch_history WHERE is_replay=1").fetchone()[0],
        "playlists":      c.execute("SELECT COUNT(*) FROM playlist_signals").fetchone()[0],
        "user_playlists": c.execute("SELECT COUNT(*) FROM user_playlists").fetchone()[0],
        "top_channels":   [r["channel_name"] for r in c.execute(
            "SELECT channel_name FROM channel_scores ORDER BY score DESC LIMIT 5").fetchall()],
        "top_videos":     [r["title"] for r in c.execute(
            "SELECT title FROM video_scores ORDER BY replay_count DESC LIMIT 3").fetchall()],
    }
    conn.close()
    return stats

def reset_db():
    path = get_db_path()
    if os.path.exists(path): os.remove(path); print(f"✓ {path} deleted")

# ── Interactive profile picker (used when running standalone) ─────────────────
def _pick_profile(prompt="Import into which profile?") -> str:
    """
    Show existing profiles and let the user pick one or type a new name.
    Returns the chosen username string.
    Used when running  python recommender.py --import  without --user.
    """
    existing = list_users()
    print(f"\n  {prompt}")
    print()
    if existing:
        for i, u in enumerate(existing, 1):
            wm  = get_warmth(u)
            cnt = get_play_count(u)
            print(f"    [{i}] {u}  ({wm}, {cnt} plays)")
    print(f"    [n] Create new profile")
    print()
    while True:
        try:
            raw = input("  Choice: ").strip()
        except (KeyboardInterrupt, EOFError):
            print(); raise SystemExit(0)
        if raw.lower() == "n":
            while True:
                try:
                    name = input("  New profile name: ").strip()
                except (KeyboardInterrupt, EOFError):
                    print(); raise SystemExit(0)
                if name:
                    return name.lower().replace(" ", "_")
        if raw.isdigit():
            idx = int(raw) - 1
            if existing and 0 <= idx < len(existing):
                return existing[idx]
        print("  Enter a number or 'n'")

# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="ytterm recommender — manage per-user taste profiles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python recommender.py --import ./Takeout
      → picks a profile interactively, then imports

  python recommender.py --import ./Takeout --user alice
      → imports directly into alice's profile

  python recommender.py --stats --user alice
  python recommender.py --list-users
  python recommender.py --reset --user alice
""")
    p.add_argument("--user",       default=None,         help="Target profile name (skips interactive picker)")
    p.add_argument("--import",     dest="takeout",       metavar="PATH", help="Path to Takeout folder")
    p.add_argument("--stats",      action="store_true",  help="Show stats for a profile")
    p.add_argument("--reset",      action="store_true",  help="Delete a profile's DB")
    p.add_argument("--list-users", action="store_true",  help="List all profiles")
    args = p.parse_args()

    if args.list_users:
        users = list_users()
        if not users:
            print("\n  No profiles found.\n")
        else:
            print(f"\n  Profiles ({len(users)}):")
            for u in users:
                wm  = get_warmth(u)
                cnt = get_play_count(u)
                db  = get_db_path(u)
                size_kb = os.path.getsize(db) // 1024 if os.path.exists(db) else 0
                print(f"    • {u:<20}  {wm:<5}  {cnt:>4} plays  {size_kb} KB")
            print()

    elif args.takeout:
        # Resolve target user — CLI flag wins, otherwise ask interactively
        if args.user:
            target = args.user
            print(f"\n  Target profile: {target}")
        else:
            target = _pick_profile("Import Takeout data into which profile?")

        set_user(target)
        init_db()  # create DB if it doesn't exist yet
        success = import_takeout(args.takeout, username=target)
        if success:
            s = get_stats()
            if s:
                print(f"\n  Profile '{target}' now has:")
                print(f"    {s['channels']} channels  ·  {s['keywords']} keywords")
                print(f"    warmth: {s['warmth']}")

    elif args.stats:
        target = args.user or _pick_profile("Show stats for which profile?")
        set_user(target)
        s = get_stats()
        if not s:
            print(f"\n  No DB found for '{target}'. Run --import first.\n")
        else:
            print(f"\n── {s['user']} ({s['warmth'].upper()}) ────────────────")
            print(f"  Plays    : {s['plays']}  (cold<10 warm<50 hot=50+)")
            print(f"  Channels : {s['channels']}  Keywords: {s['keywords']}")
            print(f"  Replays  : {s['replays']}  Playlists: {s['user_playlists']}")
            if s["top_channels"]:
                print(f"\n  Top channels:")
                for ch in s["top_channels"]: print(f"    • {ch}")
            if s["top_videos"]:
                print(f"\n  Most replayed:")
                for v in s["top_videos"]: print(f"    ▶ {v}")
            print()

    elif args.reset:
        target = args.user or _pick_profile("Reset which profile?")
        set_user(target)
        db = get_db_path(target)
        if not os.path.exists(db):
            print(f"\n  No DB for '{target}'.\n")
        else:
            try:
                confirm = input(f"  Delete {db}? [y/N]: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print(); raise SystemExit(0)
            if confirm == "y":
                reset_db()
                print(f"  ✓ Profile '{target}' deleted.\n")
            else:
                print("  Cancelled.\n")

    else:
        p.print_help()
