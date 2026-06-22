#!/usr/bin/env python3
"""
ytterm — YouTube in your terminal
Smart feed · Live Now Playing · Infinite queue
"""
import os, sys, re, json, base64, getpass, pickle, signal
import subprocess, secrets, time, platform, shutil, random, threading
import socket as _sock

SYSTEM = platform.system()
IS_WIN = SYSTEM == "Windows"
if IS_WIN:
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception: pass

SD = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SD)
try:    import recommender as REC; HAS_REC = True
except: REC = None;                HAS_REC = False

# ── Rename process so system monitor / audio mixer shows "ytterm" not "python3" ─
def _set_proc_name(name: str):
    """
    Set the process title visible in htop, GNOME System Monitor, and
    audio session managers (PulseAudio/PipeWire show the process name).
    Two methods — both needed for full coverage:
      1. /proc/self/comm  — controls the name in ps/htop/system-monitor
      2. prctl PR_SET_NAME — controls the name in most audio session managers
    """
    try:
        # Method 1: write directly to /proc/self/comm (Linux only)
        with open("/proc/self/comm", "w") as f:
            f.write(name[:15])   # kernel limits comm to 15 chars
    except Exception:
        pass
    try:
        # Method 2: prctl(PR_SET_NAME) — used by PulseAudio / PipeWire client names
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_NAME = 15
        libc.prctl(PR_SET_NAME, name.encode()[:15] + b"\x00", 0, 0, 0)
    except Exception:
        pass
    try:
        # Method 3: argv[0] — controls what shows in some process views
        import ctypes
        if sys.argv:
            argv0 = (ctypes.c_char * len(sys.argv[0])).from_address(
                id(sys.argv[0]) + 20)  # CPython string buffer offset
            # Safe fallback: just set argv[0] string value
            sys.argv[0] = name
    except Exception:
        pass

_set_proc_name("ytterm")   # rename immediately at import time

# ── ANSI ───────────────────────────────────────────────────────────────────────
E = "\033"
R  = f"{E}[0m"; B  = f"{E}[1m"; D  = f"{E}[2m"; IT = f"{E}[3m"
K  = f"{E}[30m"; RD = f"{E}[31m"; GR = f"{E}[32m"; YE = f"{E}[33m"
BL = f"{E}[34m"; MA = f"{E}[35m"; CY = f"{E}[36m"; WH = f"{E}[37m"
K2 = f"{E}[90m"; R2 = f"{E}[91m"; G2 = f"{E}[92m"; Y2 = f"{E}[93m"
B2 = f"{E}[94m"; M2 = f"{E}[95m"; C2 = f"{E}[96m"; W2 = f"{E}[97m"
bK = f"{E}[40m"; bR = f"{E}[41m"; bG = f"{E}[42m"; bY = f"{E}[43m"
bB = f"{E}[44m"; bM = f"{E}[45m"; bC = f"{E}[46m"; bW = f"{E}[47m"
bD = f"{E}[100m"

# ── Terminal size (always fresh) ───────────────────────────────────────────────
def TW():
    """Terminal width — read live so zoom/resize is instant."""
    try:    return max(40, os.get_terminal_size().columns)
    except: return 80

def TH():
    """Terminal height."""
    try:    return max(10, os.get_terminal_size().lines)
    except: return 24

def cls():
    """Instant clear — no subprocess, no flash."""
    sys.stdout.write(f"{E}[H{E}[2J{E}[3J")
    sys.stdout.flush()

def show_cur(): sys.stdout.write(f"{E}[?25h"); sys.stdout.flush()
def hide_cur(): sys.stdout.write(f"{E}[?25l"); sys.stdout.flush()

# ── Box drawing (width computed fresh each call) ───────────────────────────────
def _plain(s):
    """Strip ANSI escapes to get visible length."""
    return re.sub(r'\033\[[^m]*m', '', s)

def _fit(s, n):
    """Truncate string so visible length == n."""
    while len(_plain(s)) > n and s:
        s = s[:-1]
    return s

def box_top(w, title=""):
    if title:
        t  = f" {title} "
        pad = w - 2 - len(t)
        l, r = pad // 2, pad - pad // 2
        return f"{B2}┏{'━'*l}{W2}{B}{t}{R}{B2}{'━'*r}┓{R}"
    return f"{B2}{B}┏{'━'*(w-2)}┓{R}"

def box_mid(w):
    return f"{B2}{D}┠{'─'*(w-2)}┨{R}"

def box_bot(w):
    return f"{B2}{B}┗{'━'*(w-2)}┛{R}"

def box_row(content, w):
    """Print a box row, padding/clipping content to fit width."""
    visible = len(_plain(content))
    pad     = max(0, w - 2 - visible - 2)  # 2 for "┃ " prefix/suffix
    clipped = _fit(content, w - 6) if visible > w - 4 else content
    return f"{B2}{B}┃{R} {clipped}{' ' * pad} {B2}{B}┃{R}"

# ── Paths ──────────────────────────────────────────────────────────────────────
CLIENT_SEC = os.path.join(SD, "client_secret.json")
SALT_FILE  = os.path.join(SD, "salt.bin")
COOKIES    = os.path.join(SD, "cookies.txt")
SCOPES     = ["https://www.googleapis.com/auth/youtube.readonly"]

PER_PAGE     = 10
INITIAL_LOAD = 50
PAGE_LOAD    = 10
PRE_AHEAD    = 2

# ── State ──────────────────────────────────────────────────────────────────────
MODE         = "fresh"
TAB          = "foryou"
CURRENT_USER = "default"
FEED_BUF     = {"subs": [], "liked": [], "home": [], "foryou": []}
FEED_DONE    = {k: False for k in FEED_BUF}
FEED_SEEN    = set()
FEED_SEEDS   = []
PAGE_OFFSET  = 0
_pf_thread   = None
_yt          = None

# ── Auth storage in DB ─────────────────────────────────────────────────────────
# Password hash lives INSIDE the user's own taste_<user>.db — never in a
# separate file that could be copied or inspected independently.
# Schema:  app_auth(salt TEXT, hash TEXT)
#   salt  — 32 random bytes stored as hex
#   hash  — PBKDF2-HMAC-SHA256(password, salt, 600_000 iters) stored as hex
# The Fernet key for the OAuth token is derived from the same password,
# so the .enc file is useless without knowing the password.

def _tok_path():
    return os.path.join(SD, f"token_{CURRENT_USER}.enc")

def _ensure_auth_table(conn):
    """Add app_auth table if it does not exist yet (migration-safe)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_auth (
            id    INTEGER PRIMARY KEY CHECK (id = 1),
            salt  TEXT NOT NULL,
            hash  TEXT NOT NULL
        )
    """)
    conn.commit()

def _pw_hash(password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 — slow by design to resist brute force."""
    import hashlib
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        600_000,   # 600 k iterations ≈ 0.3 s on modern hardware
        dklen=32
    )

def _fernet_key_from_pw(password: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible 32-byte URL-safe base64 key from password."""
    raw = _pw_hash(password, salt)
    return base64.urlsafe_b64encode(raw)

def auth_has_password() -> bool:
    """Return True if this profile already has a password set in its DB."""
    if not HAS_REC or not REC.db_exists(): return False
    try:
        conn = REC.get_db()
        _ensure_auth_table(conn)
        row = conn.execute("SELECT id FROM app_auth LIMIT 1").fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False

def auth_set_password(password: str):
    """Hash password and store salt+hash in the user's DB. Overwrites any old one."""
    salt = secrets.token_bytes(32)
    h    = _pw_hash(password, salt)
    conn = REC.get_db()
    _ensure_auth_table(conn)
    conn.execute("DELETE FROM app_auth")                        # clear old
    conn.execute("INSERT INTO app_auth(id,salt,hash) VALUES(1,?,?)",
                 (salt.hex(), h.hex()))
    conn.commit(); conn.close()

def auth_check_password(password: str) -> bool:
    """Return True if password matches what is stored in the DB."""
    try:
        conn = REC.get_db()
        _ensure_auth_table(conn)
        row  = conn.execute("SELECT salt, hash FROM app_auth LIMIT 1").fetchone()
        conn.close()
        if not row: return True   # no password set yet → always pass
        salt = bytes.fromhex(row[0])
        h    = _pw_hash(password, salt)
        return h.hex() == row[1]
    except Exception:
        return False

def _get_auth_salt() -> bytes:
    """Return the stored salt, or generate+store a new one."""
    try:
        conn = REC.get_db()
        _ensure_auth_table(conn)
        row  = conn.execute("SELECT salt FROM app_auth LIMIT 1").fetchone()
        conn.close()
        if row: return bytes.fromhex(row[0])
    except Exception: pass
    return secrets.token_bytes(32)   # fallback random salt

def enc_tok(creds, pw):
    """Encrypt Google OAuth credentials with Fernet key derived from password."""
    from cryptography.fernet import Fernet
    salt = _get_auth_salt()
    key  = _fernet_key_from_pw(pw, salt)
    data = Fernet(key).encrypt(pickle.dumps(creds))
    path = _tok_path()
    open(path, "wb").write(data)
    if not IS_WIN: os.chmod(path, 0o600)

def dec_tok(pw):
    """Decrypt Google OAuth credentials. Returns None on wrong password."""
    from cryptography.fernet import Fernet, InvalidToken
    path = _tok_path()
    if not os.path.exists(path): return None
    try:
        salt = _get_auth_salt()
        key  = _fernet_key_from_pw(pw, salt)
        return pickle.loads(Fernet(key).decrypt(open(path, "rb").read()))
    except (InvalidToken, Exception):
        return None

def _ask_pw(prompt: str) -> str:
    """
    Read a password without echoing.
    Print our own prompt first (so it's always visible after cls()),
    then call getpass.getpass("") with EMPTY prompt string — getpass
    opens /dev/tty internally and handles echo-off correctly on all
    terminals including kitty, alacritty, and xterm on Arch Linux.
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        return getpass.getpass("")
    except KeyboardInterrupt:
        sys.stdout.write("\n"); sys.exit(0)
    except Exception:
        return getpass.getpass(prompt)   # last resort with its own prompt

# ── Auth ───────────────────────────────────────────────────────────────────────
def app_lock() -> str:
    """
    App-lock: runs for ALL named profiles (both fresh and personal mode).
    Anonymous sessions skip the lock entirely.

    First run  → user creates a password; PBKDF2 hash stored in their DB.
    Later runs → typed password verified against stored hash.

    Returns the plain-text password so authenticate() can use it to
    derive the Fernet key for the OAuth token file.
    If the profile has no lock set yet, prompts to create one.
    """
    # Anonymous sessions never need a lock
    if CURRENT_USER == "__anon__":
        return ""

    # Make sure the DB + app_auth table exist before we read from them
    if HAS_REC:
        try:
            REC.init_db()
            conn = REC.get_db()
            _ensure_auth_table(conn)
            conn.close()
        except Exception:
            pass

    w = TW()
    if auth_has_password():
        # ── Returning user: verify password ──────────────────────────────────
        print()
        print(box_top(w, "🔐  ytterm lock"))
        print(box_row(f"  Profile: {B}{W2}{CURRENT_USER}{R}", w))
        print(box_bot(w))
        attempts = 0
        while True:
            pw = _ask_pw("  Password: ")
            if auth_check_password(pw):
                print(f"  {G2}✓ Unlocked{R}\n")
                return pw
            attempts += 1
            if attempts >= 3:
                print(f"  {R2}Too many wrong attempts. Exiting.{R}")
                sys.exit(1)
            print(f"  {R2}Wrong password  ({attempts}/3){R}")
    else:
        # ── First time: create a password ────────────────────────────────────
        print()
        print(box_top(w, "🔐  Create profile password"))
        print(box_row(f"  Profile: {B}{W2}{CURRENT_USER}{R}", w))
        print(box_row(f"  {D}Stored as a secure hash inside your profile DB.{R}", w))
        print(box_row(f"  {D}Cannot be recovered if forgotten.{R}", w))
        print(box_bot(w))
        while True:
            pw = _ask_pw("  New password:     ")
            if not pw:
                print(f"  {R2}Password cannot be empty.{R}"); continue
            pw2 = _ask_pw("  Confirm password: ")
            if pw == pw2:
                auth_set_password(pw)
                print(f"  {G2}✓ Password saved in profile DB{R}\n")
                return pw
            print(f"  {R2}Passwords don't match, try again.{R}")


def authenticate(pw: str = "") -> object:
    """
    Google OAuth layer — personal mode only.
    pw is the app-lock password, used to derive the Fernet decryption key
    for the stored OAuth token file.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request

    if not os.path.exists(CLIENT_SEC):
        print(f"\n{R2}client_secret.json not found — run: python setup.py{R}")
        sys.exit(1)

    tok   = _tok_path()
    creds = dec_tok(pw) if os.path.exists(tok) else None

    if creds and creds.valid:
        pass
    elif creds and creds.expired and creds.refresh_token:
        print(f"  {D}Refreshing Google token…{R}")
        creds.refresh(Request())
        enc_tok(creds, pw)
    else:
        print(f"  {D}Opening browser for Google login…{R}")
        flow  = InstalledAppFlow.from_client_secrets_file(CLIENT_SEC, SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        enc_tok(creds, pw)
        print(f"  {G2}✓ Token saved{R}")

    return build("youtube", "v3", credentials=creds)

# ── yt-dlp helpers ─────────────────────────────────────────────────────────────
def _ydl():
    cmd = ["yt-dlp", "--no-warnings"]
    if os.path.exists(COOKIES): cmd += ["--cookies", COOKIES]
    return cmd

YT_RE = [
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?(?:.*&)?v=([\w-]{11})",
    r"(?:https?://)?youtu\.be/([\w-]{11})",
    r"(?:https?://)?music\.youtube\.com/watch\?(?:.*&)?v=([\w-]{11})",
    r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([\w-]{11})",
]
def vid_id_from(t):
    for p in YT_RE:
        m = re.search(p, t)
        if m: return m.group(1)
    return None

def _parse_json(raw, skip_id=None):
    """Parse yt-dlp -J output into list of video dicts. Handles every output shape."""
    if not raw or not raw.strip(): return []
    try:    data = json.loads(raw)
    except: return []
    if not data or not isinstance(data, dict): return []
    if "entries" not in data: data = {"entries": [data]}  # single video
    out = []
    for e in (data.get("entries") or []):
        if not e or not isinstance(e, dict): continue
        vid = e.get("id", "")
        if not vid:
            u = e.get("url", "")
            m = re.search(r"[?&]v=([\w-]{11})", u)
            if m: vid = m.group(1)
        if not vid or vid == skip_id: continue
        title = (e.get("title") or "").strip()
        if not title or title in ("[Private video]", "[Deleted video]"): continue
        ch = e.get("channel") or e.get("uploader") or "Unknown"
        out.append({"id": vid, "title": title, "channel": ch,
                    "url": f"https://www.youtube.com/watch?v={vid}"})
    return out

def fetch_related(vid_id, n=10):
    """YouTube RD mix → fallback ytsearch."""
    url = f"https://www.youtube.com/watch?v={vid_id}&list=RD{vid_id}"
    try:
        res = subprocess.run(
            _ydl() + ["--flat-playlist", "--playlist-end", str(n),
                      "--extractor-args", "youtube:skip=authcheck", "-J", url],
            capture_output=True, text=True, timeout=30)
        out = _parse_json(res.stdout, skip_id=vid_id)
        if len(out) >= 3: return out[:n]
    except Exception: pass
    title = _title_from_buf(vid_id)
    if title: return yt_search(title, n)
    return []

def yt_search(query, n=10):
    try:
        res = subprocess.run(
            _ydl() + [f"ytsearch{n}:{query}", "--flat-playlist", "-J"],
            capture_output=True, text=True, timeout=30)
        return _parse_json(res.stdout)[:n]
    except Exception: return []

def vid_info(vid_id):
    try:
        res = subprocess.run(
            _ydl() + ["--skip-download", "-J",
                      f"https://www.youtube.com/watch?v={vid_id}"],
            capture_output=True, text=True, timeout=20)
        d = json.loads(res.stdout)
        return {"id": vid_id, "title": d.get("title", "Unknown"),
                "channel": d.get("channel") or d.get("uploader") or "Unknown",
                "url": f"https://www.youtube.com/watch?v={vid_id}"}
    except Exception:
        return {"id": vid_id, "title": "Unknown", "channel": "Unknown",
                "url": f"https://www.youtube.com/watch?v={vid_id}"}

def _title_from_buf(vid_id):
    for buf in FEED_BUF.values():
        for v in buf:
            if v["id"] == vid_id: return v["title"]
    return None

# ── For You seeds ──────────────────────────────────────────────────────────────
COLD_Q = [
    "top songs 2024", "trending music india", "lofi hip hop chill",
    "best telugu songs 2024", "best tamil songs 2024",
    "english hits 2024", "bollywood new songs", "viral youtube videos",
    "best workout music", "nature relaxing music 2024",
]

def _pref_queries():
    """Build search queries from user's actual taste DB."""
    if not HAS_REC or not REC.db_exists(): return []
    try:
        conn = REC.get_db(); c = conn.cursor()
        channels = [r[0] for r in c.execute(
            "SELECT channel_name FROM channel_scores ORDER BY score DESC LIMIT 6"
        ).fetchall()]
        kws = [r[0] for r in c.execute(
            "SELECT keyword FROM keyword_scores ORDER BY score DESC LIMIT 10"
        ).fetchall()]
        top_titles = [r[0] for r in c.execute(
            "SELECT title FROM video_scores ORDER BY replay_count DESC, score DESC LIMIT 5"
        ).fetchall()]
        conn.close()
        q = []
        for ch in channels[:4]:
            q.append(f"{ch} songs")
        if len(kws) >= 2: q.append(f"{kws[0]} {kws[1]} music")
        if len(kws) >= 4: q.append(f"{kws[2]} {kws[3]} songs")
        for t in top_titles[:3]:
            q.append(re.sub(r'\|.*', '', t).strip()[:40])
        return q
    except Exception: return []

def _refill_seeds():
    pref = _pref_queries()
    if pref:
        random.shuffle(pref); FEED_SEEDS.extend(pref)
    if HAS_REC and REC.db_exists():
        try:
            _, vids = REC.get_seed_videos(n=6)
            random.shuffle(vids); FEED_SEEDS.extend(vids)
        except Exception: pass
    cold = COLD_Q.copy(); random.shuffle(cold)
    FEED_SEEDS.extend(cold[:4])

def _foryou_batch(n=PAGE_LOAD):
    warmth = "cold"
    if HAS_REC and REC.db_exists():
        try: warmth = REC.get_warmth()
        except Exception: pass
    new = []; tried = 0
    while len(new) < n:
        tried += 1
        if tried > 30: break
        if not FEED_SEEDS: _refill_seeds()
        if not FEED_SEEDS: break
        seed = FEED_SEEDS.pop(0)
        cands = (fetch_related(seed, 15)
                 if re.match(r'^[\w-]{11}$', seed)
                 else yt_search(seed, 15))
        for v in cands:
            if v["id"] not in FEED_SEEN:
                new.append(v); FEED_SEEN.add(v["id"])
            if len(new) >= n: break
    if new and warmth != "cold" and HAS_REC and REC.db_exists():
        try: new = REC.rank_videos(new, allow_seen=True)
        except Exception: random.shuffle(new)
    else:
        random.shuffle(new)
    return new[:n]

# ── Feed loaders ───────────────────────────────────────────────────────────────
def _reset(tab):
    FEED_BUF[tab] = []; FEED_DONE[tab] = False
    if tab == "foryou": FEED_SEEN.clear(); FEED_SEEDS.clear()

def _add(tab, vids):
    FEED_BUF[tab].extend(vids)

def load_foryou(silent=False):
    _reset("foryou")
    if HAS_REC:
        try: REC.init_db()
        except Exception: pass
    if not silent: print(f"  {D}Building For You…{R}", flush=True)
    _refill_seeds()
    _add("foryou", _foryou_batch(INITIAL_LOAD))

def load_home(silent=False):
    _reset("home"); FEED_DONE["home"] = True
    if not silent: print(f"  {D}Fetching trending…{R}", flush=True)
    try:
        res = subprocess.run(
            _ydl() + ["--flat-playlist", "--playlist-end", "50",
                      "--extractor-args", "youtubetab:approximate_date",
                      "-J", "https://www.youtube.com/feed/trending"],
            capture_output=True, text=True, timeout=45)
        _add("home", _parse_json(res.stdout))
    except Exception: pass

_subs_ids = []; _subs_idx = 0
def _more_subs(yt, n=PAGE_LOAD):
    global _subs_idx
    out = []
    while len(out) < n and _subs_idx < len(_subs_ids):
        cid = _subs_ids[_subs_idx]; _subs_idx += 1
        try:
            ch = yt.channels().list(part="contentDetails", id=cid).execute()
            if not ch.get("items"): continue
            plid = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            pl   = yt.playlistItems().list(part="snippet", playlistId=plid, maxResults=3).execute()
            for item in pl.get("items", []):
                s = item["snippet"]; vid = s["resourceId"].get("videoId")
                if not vid: continue
                out.append({"id": vid, "title": s["title"], "channel": s["channelTitle"],
                            "published": s.get("publishedAt", ""),
                            "url": f"https://www.youtube.com/watch?v={vid}"})
        except Exception: continue
    if _subs_idx >= len(_subs_ids): FEED_DONE["subs"] = True
    return out

def load_subs(yt, silent=False):
    global _subs_ids, _subs_idx
    _reset("subs")
    if not silent: print(f"  {D}Loading subscriptions…{R}", flush=True)
    ids, tok = [], None
    while True:
        kw = dict(part="snippet", mine=True, maxResults=50)
        if tok: kw["pageToken"] = tok
        resp = yt.subscriptions().list(**kw).execute()
        for i in resp.get("items", []):
            ids.append(i["snippet"]["resourceId"]["channelId"])
        tok = resp.get("nextPageToken")
        if not tok or len(ids) >= 200: break
    _subs_ids = ids; _subs_idx = 0
    vids = _more_subs(yt, INITIAL_LOAD)
    if HAS_REC and REC.db_exists():
        try: vids = REC.rank_videos(vids)
        except Exception: pass
    _add("subs", vids)

_liked_tok = None
def _more_liked(yt, n=PAGE_LOAD):
    global _liked_tok
    out = []
    while len(out) < n:
        kw = dict(part="snippet", myRating="like", maxResults=50)
        if _liked_tok: kw["pageToken"] = _liked_tok
        try: resp = yt.videos().list(**kw).execute()
        except Exception: FEED_DONE["liked"] = True; break
        for item in resp.get("items", []):
            s = item["snippet"]
            out.append({"id": item["id"], "title": s["title"],
                        "channel": s["channelTitle"],
                        "published": s.get("publishedAt", ""),
                        "url": f"https://www.youtube.com/watch?v={item['id']}"})
        _liked_tok = resp.get("nextPageToken")
        if not _liked_tok: FEED_DONE["liked"] = True; break
    return out

def load_liked(yt, silent=False):
    global _liked_tok; _reset("liked"); _liked_tok = None
    if not silent: print(f"  {D}Loading liked…{R}", flush=True)
    vids = _more_liked(yt, INITIAL_LOAD)
    vids.sort(key=lambda v: v.get("published", ""), reverse=True)
    _add("liked", vids)

# ── Background prefetch ─────────────────────────────────────────────────────────
def _pf_worker(tab):
    try:
        if   tab == "foryou": _add("foryou", _foryou_batch(PAGE_LOAD))
        elif tab == "subs"  and _yt: _add("subs",  _more_subs(_yt,  PAGE_LOAD))
        elif tab == "liked" and _yt: _add("liked", _more_liked(_yt, PAGE_LOAD))
    except Exception: pass

def _start_pf(tab):
    global _pf_thread
    if _pf_thread and _pf_thread.is_alive(): return
    _pf_thread = threading.Thread(target=_pf_worker, args=(tab,), daemon=True)
    _pf_thread.start()

def _wait_pf():
    if _pf_thread and _pf_thread.is_alive():
        _pf_thread.join(timeout=30)

# ── IPC helper ─────────────────────────────────────────────────────────────────
def _ipc(sock_path, cmd):
    """Send command to mpv IPC socket, return data or None."""
    try:
        rid = random.randint(10000, 99999)
        msg = json.dumps({"command": cmd, "request_id": rid}) + "\n"
        s   = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
        s.settimeout(3.0); s.connect(sock_path); s.sendall(msg.encode())
        buf = b""
        for _ in range(40):
            chunk = s.recv(4096)
            if not chunk: break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    obj = json.loads(line)
                    if obj.get("request_id") == rid:
                        s.close(); return obj.get("data")
                except Exception: pass
        s.close()
    except Exception: pass
    return None

# ── mpv playlist builder ───────────────────────────────────────────────────────
def _m3u(q):
    p = os.path.join(SD, ".ytterm_queue.m3u8")
    lines = ["#EXTM3U"]
    for v in q:
        lines.append(f"#EXTINF:-1,{v['title'].replace(',', '')}")
        lines.append(v["url"])
    open(p, "w", encoding="utf-8").write("\n".join(lines))
    return p

def _keybinds():
    p = os.path.join(SD, ".ytterm_keys.conf")
    open(p, "w").write(
        "n playlist-next\n"
        "p playlist-prev\n"
        "l ab-loop\n"
        "L cycle-values loop-playlist inf no\n"
        "q quit 4\n")
    return p

# ── Now Playing UI ─────────────────────────────────────────────────────────────
def _np_draw(q, idx, src, to_tty=False):
    """
    Draw the Now Playing screen. Always reads terminal size fresh.
    to_tty=True writes to /dev/tty so it can coexist with mpv's stdout usage.
    """
    w = TW(); h = TH()
    v = q[min(idx, len(q) - 1)]

    src_badge = {
        "foryou": f"{bM}{W2} ✨ For You {R}",
        "subs":   f"{bB}{W2} 📋 Subs {R}",
        "liked":  f"{bR}{W2} ❤  Liked {R}",
        "home":   f"{bY}{K} 🔥 Trending {R}",
        "url":    f"{bM}{W2} 🔗 URL {R}",
    }.get(src, f"{bG}{K} 🎵 {src.replace('pl:','')} {R}"
               if src.startswith("pl:") else "")

    lines = []
    lines.append("")
    lines.append(box_top(w, "NOW PLAYING"))
    lines.append(box_row(f"{src_badge}  {D}track {idx+1}/{len(q)}{R}", w))
    lines.append(box_mid(w))

    # wrap title
    title_text = v["title"]
    wrap        = w - 8
    words       = title_text.split()
    tl          = []; cur = ""
    for word in words:
        test = (cur + " " + word).strip()
        if len(test) <= wrap: cur = test
        else:
            if cur: tl.append(cur)
            cur = word
    if cur: tl.append(cur)
    if not tl: tl = [title_text[:wrap]]

    lines.append(box_row(f"{G2}{B}▶  {tl[0]}{R}", w))
    for ln in tl[1:]:
        lines.append(box_row(f"   {W2}{B}{ln}{R}", w))
    lines.append(box_row(f"   {D}by {v['channel']}{R}", w))
    lines.append(box_mid(w))

    # queue — show context around current track
    lines.append(box_row(f"{B}{W2}Queue{R}", w))
    start = max(0, idx - 2)
    shown = 0
    for j in range(start, len(q)):
        if shown >= 6: break
        qt = q[j]["title"]
        if len(qt) > w - 8: qt = qt[:w-9] + "…"
        if j == idx:
            lines.append(box_row(f"  {G2}{B}▶ {qt}{R}", w))
        elif j < idx:
            lines.append(box_row(f"  {D}↑ {qt}{R}", w))
        else:
            lines.append(box_row(f"  {D}◦ {qt}{R}", w))
        shown += 1
    rem = len(q) - start - shown
    if rem > 0:
        lines.append(box_row(f"  {D}…+{rem} more  ⟳ auto-refilling{R}", w))
    lines.append(box_mid(w))

    keys = (f"{G2}n{R}=next  {G2}p{R}=prev  {G2}l{R}=loop  "
            f"{G2}L{R}=loop-all  {G2}Space{R}=pause  "
            f"{G2}←/→{R}=seek  {G2}9/0{R}=vol  {G2}m{R}=mute  {G2}q{R}=back")
    lines.append(box_row(keys, w))
    lines.append(box_bot(w))
    lines.append("")

    if to_tty:
        # Write our UI panel directly to /dev/tty (the actual terminal device).
        # mpv's --term-status-msg line uses stdout with \r overwrite at the
        # current cursor position — it won't touch our absolute-positioned rows.
        # Strategy:
        #   - Hide cursor during redraw to prevent flicker
        #   - Move to row 1, erase+write each line with absolute positioning
        #   - Move cursor to row len(lines)+2 (below our panel, above mpv bar)
        #   - Show cursor again
        try:
            fd  = os.open("/dev/tty", os.O_WRONLY)
            out = [b"\033[?25l"]          # hide cursor
            for row, ln in enumerate(lines, 1):
                out.append(
                    f"\033[{row};1H\033[2K{ln}".encode("utf-8", errors="replace")
                )
            # Position cursor below our panel for mpv's progress line
            out.append(f"\033[{len(lines)+2};1H".encode())
            out.append(b"\033[?25h")      # show cursor
            os.write(fd, b"".join(out))
            os.close(fd)
        except Exception:
            pass
    else:
        # Initial draw before mpv starts — stdout is ours, do a full clear
        cls()
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()

# ── Queue refill thread ─────────────────────────────────────────────────────────
def _refill_thread(q, seen, ipc_path, stop):
    """Keeps mpv playlist full forever using IPC loadfile append."""
    for _ in range(24):
        if stop.is_set(): return
        if os.path.exists(ipc_path): break
        time.sleep(0.5)
    if not os.path.exists(ipc_path): return

    seed_i = [0]; last_t = [time.time()]

    def refill():
        if not q: return
        seed = q[seed_i[0] % len(q)]["id"]; seed_i[0] += 1
        more = fetch_related(seed, 6)
        added = 0
        for v in more:
            if stop.is_set(): break
            if v["id"] not in seen:
                q.append(v); seen.add(v["id"])
                _ipc(ipc_path, ["loadfile", v["url"], "append"])
                added += 1
        if added == 0: seed_i[0] += 2  # nothing new → skip ahead

    while not stop.is_set():
        time.sleep(4)
        if stop.is_set(): break
        pos   = _ipc(ipc_path, ["get_property", "playlist-pos"])
        total = _ipc(ipc_path, ["get_property", "playlist-count"])
        now   = time.time()
        if pos is not None and total is not None:
            if int(total) - int(pos) - 1 <= 2:
                refill(); last_t[0] = now
        elif now - last_t[0] > 45:
            refill(); last_t[0] = now

# ── Progress bar (drawn by Python, not mpv) ────────────────────────────────────
def _draw_progress(pct, pos_s, dur_s, paused, row):
    """
    Draw a single-line ASCII progress bar at absolute terminal row `row`.
    Writes to /dev/tty so it never conflicts with mpv output.

    pct   — float 0-100
    pos_s — current position in seconds (float) or None
    dur_s — total duration in seconds (float) or None
    paused — bool
    row   — terminal row to draw at (bottom of screen)
    """
    w = TW()

    def fmt_time(s):
        if s is None: return "--:--"
        s = max(0, int(s))
        return f"{s//60:02d}:{s%60:02d}"

    pos_str = fmt_time(pos_s)
    dur_str = fmt_time(dur_s)
    pct_str = f"{int(pct):3d}%" if pct is not None else "  ?"
    sym     = f"{Y2}⏸{R}" if paused else f"{G2}▶{R}"

    # Calculate bar width: total width minus fixed parts
    # " ▶  MM:SS ━━━━━━━ MM:SS  100% "
    fixed = 2 + 2 + 5 + 1 + 5 + 2 + 4 + 2   # sym + spaces + times + pct
    bar_w = max(10, w - fixed)

    filled = int(bar_w * (pct or 0) / 100)
    empty  = bar_w - filled
    bar    = f"{G2}{'━' * filled}{R}{K2}{'━' * empty}{R}"

    line = f" {sym}  {W2}{pos_str}{R} {bar} {W2}{dur_str}{R}  {Y2}{pct_str}{R} "

    try:
        fd  = os.open("/dev/tty", os.O_WRONLY)
        out = (f"[{row};1H"   # move to progress row
               f"[2K"          # erase the line
               + line)
        os.write(fd, out.encode("utf-8", errors="replace"))
        os.close(fd)
    except Exception:
        pass

# ── Progress poller thread ──────────────────────────────────────────────────────
def _progress_thread(ipc_path, stop):
    """
    Polls mpv every second via IPC for playback position, duration, and
    pause state, then draws a progress bar at the bottom of the terminal.
    Completely independent of mpv's own terminal output.
    """
    # Wait for IPC socket
    for _ in range(24):
        if stop.is_set(): return
        if os.path.exists(ipc_path): break
        time.sleep(0.5)
    if not os.path.exists(ipc_path): return

    time.sleep(1.0)   # let mpv fully initialize

    while not stop.is_set():
        time.sleep(1)
        if stop.is_set(): break
        # Query all progress properties in three quick IPC calls
        pos    = _ipc(ipc_path, ["get_property", "time-pos"])
        dur    = _ipc(ipc_path, ["get_property", "duration"])
        pct    = _ipc(ipc_path, ["get_property", "percent-pos"])
        paused = _ipc(ipc_path, ["get_property", "pause"])
        if pos is None and dur is None:
            continue   # mpv not ready yet
        row = max(2, TH())   # bottom row of terminal
        _draw_progress(pct, pos, dur, bool(paused), row)

# ── UI updater thread ──────────────────────────────────────────────────────────
def _ui_thread(q, src, ipc_path, stop):
    """
    Listens for mpv property-change events on a PERSISTENT open socket.

    Why persistent instead of polling?
      - Media keys (XF86AudioNext etc.) trigger MPRIS signals that mpv handles
        internally in <10ms. A 1-second polling loop misses most of these.
      - observe_property makes mpv push an event JSON line the instant
        playlist-pos changes — no matter what caused the change.

    Flow:
      1. Wait for the IPC socket file to appear (mpv takes ~1s to start)
      2. Open one persistent TCP-like Unix socket connection
      3. Send observe_property for playlist-pos (id=1) and media-title (id=2)
      4. Read lines forever — JSON events arrive as mpv pushes them
      5. On playlist-pos change → redraw Now Playing panel in place
    """
    # Wait for mpv IPC socket to appear (max 12 seconds)
    for _ in range(24):
        if stop.is_set(): return
        if os.path.exists(ipc_path): break
        time.sleep(0.5)
    if not os.path.exists(ipc_path): return

    # Give mpv one extra second to fully initialize before we connect
    time.sleep(1.0)

    last_pos = [None]

    def run_observer():
        """Open socket, subscribe, read event loop. Returns on error."""
        try:
            sock = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect(ipc_path)
            sock.settimeout(None)   # blocking reads from here on

            # Subscribe to playlist position changes (id=1)
            # mpv will push {"event":"property-change","id":1,"data":<int>} instantly
            sock.sendall(json.dumps(
                {"command": ["observe_property", 1, "playlist-pos"]}
            ).encode() + b"\n")

            buf = b""
            while not stop.is_set():
                try:
                    sock.settimeout(2.0)   # don't block forever — check stop flag
                    chunk = sock.recv(4096)
                except _sock.timeout:
                    continue               # normal — just re-check stop flag
                except Exception:
                    break                  # socket died → reconnect outer loop

                if not chunk: break        # mpv closed connection (track loading?)
                buf += chunk

                # Process every complete JSON line
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip(): continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue

                    # We only care about property-change events for id=1 (playlist-pos)
                    if (ev.get("event") == "property-change"
                            and ev.get("id") == 1
                            and ev.get("data") is not None):
                        pos = int(ev["data"])
                        if pos != last_pos[0] and 0 <= pos < len(q):
                            last_pos[0] = pos
                            try: _np_draw(q, pos, src, to_tty=True)
                            except Exception: pass

            sock.close()
        except Exception:
            pass

    # Outer reconnect loop: if socket drops (e.g. during playlist transition)
    # wait 2 seconds and reconnect. This also catches the initial connect.
    while not stop.is_set():
        run_observer()
        if stop.is_set(): break
        time.sleep(2.0)  # wait before reconnect attempt

# ── Play ───────────────────────────────────────────────────────────────────────
def play(queue, start=0, src="related"):
    if not queue: return
    if not shutil.which("mpv"):
        print(f"{R2}mpv not found — run setup.py{R}"); sys.exit(1)

    ipc  = f"/tmp/ytterm_{os.getpid()}.sock"
    q    = list(queue)
    seen = {v["id"] for v in q}
    m3u  = _m3u(q)
    keys = _keybinds()

    # Draw initial Now Playing screen on stdout before mpv starts
    _np_draw(q, start, src)

    # Build yt-dlp extra args for cookies (if available)
    ytdl_opts = []
    if os.path.exists(COOKIES):
        ytdl_opts = [f"--ytdl-raw-options=cookies={COOKIES}"]

    cmd = [
        "mpv",
        "--no-video",
        "--ytdl",
        "--ytdl-format=bestaudio/best",
        # --really-quiet silences ALL mpv terminal output (metadata, codec
        # info, status lines, everything). We draw our own progress bar in
        # Python via IPC polling, so we don't need mpv to print anything.
        "--really-quiet",
        f"--input-ipc-server={ipc}",   # IPC socket for progress + track change
        f"--input-conf={keys}",         # n/p/l/L/q/Space/arrow keybinds
        f"--playlist-start={start}",
        *ytdl_opts,
        m3u,
    ]

    stop = threading.Event()
    # Thread 1: keep queue full by appending related tracks
    threading.Thread(target=_refill_thread,  args=(q, seen, ipc, stop), daemon=True).start()
    # Thread 2: watch for track changes (media keys, n/p) → redraw Now Playing
    threading.Thread(target=_ui_thread,      args=(q, src, ipc, stop),  daemon=True).start()
    # Thread 3: poll time/duration/pause → draw progress bar at bottom of screen
    threading.Thread(target=_progress_thread, args=(ipc, stop),          daemon=True).start()

    rc = 0
    try:    rc = subprocess.run(cmd, stderr=subprocess.DEVNULL).returncode
    except KeyboardInterrupt: rc = 4
    finally:
        stop.set()
        for p in [m3u, keys]:
            try: os.remove(p)
            except Exception: pass
        try: os.remove(ipc)
        except Exception: pass

    if HAS_REC and REC.db_exists() and q:
        v = q[min(start, len(q) - 1)]
        try:
            if rc == 4: REC.log_skip(v)
            else:       REC.log_play(v)
        except Exception: pass

# ── Feed UI ────────────────────────────────────────────────────────────────────
def _warmth_chip():
    if not HAS_REC or not REC.db_exists(): return ""
    try:
        wm = REC.get_warmth(); p = REC.get_play_count()
        return {"cold": f" {Y2}❄{p}{R}", "warm": f" {C2}～{p}{R}", "hot": f" {G2}🔥{R}"}.get(wm, "")
    except Exception: return ""

def _tab_bar(w):
    if MODE == "personal":
        defs = [("subs","S","Subs"), ("foryou","F","For You"),
                ("liked","L","Liked"), ("home","H","Trending")]
    else:
        defs = [("foryou","F","For You"), ("home","H","Trending")]
    parts = []
    for key, letter, label in defs:
        if TAB == key: parts.append(f"{bM}{W2}{B} {letter}:{label} {R}")
        else:          parts.append(f"{bD}{K2}{D} {letter}:{label} {R}")
    return "  " + "  ".join(parts)

def render_feed():
    w    = TW()   # fresh width every render — handles zoom/resize
    buf  = FEED_BUF[TAB]
    pg   = buf[PAGE_OFFSET: PAGE_OFFSET + PER_PAGE]
    pgn  = PAGE_OFFSET // PER_PAGE + 1
    fetching = _pf_thread and _pf_thread.is_alive()

    cls()  # instant, no flash

    # ─── header ───────────────────────────────────────────────────────
    ck  = f"{G2}● cookies{R}" if os.path.exists(COOKIES) else f"{R2}○ no cookies{R}"
    usr = f"{C2}{B}{CURRENT_USER}{R}"
    print(box_top(w, "ytterm"))
    print(box_row(f"  {usr}{_warmth_chip()}   {ck}", w))
    print(box_mid(w))
    print(box_row(_tab_bar(w), w))
    print(box_mid(w))

    # ─── content ──────────────────────────────────────────────────────
    if not pg:
        if TAB == "home" and not os.path.exists(COOKIES):
            print(box_row(f"{R2}Trending needs cookies.txt{R}", w))
            print(box_row(f"{D}Export from browser → save as cookies.txt here{R}", w))
        elif TAB == "foryou":
            print(box_row(f"{Y2}⏳ Building For You… press r to retry{R}", w))
        else:
            print(box_row(f"{D}Empty — press r to refresh{R}", w))
    else:
        spin = f"  {D}⟳ fetching…{R}" if fetching else ""
        if TAB == "foryou" and LAST_SEARCH:
            srch = f"  {B2}🔍 {LAST_SEARCH}{R}"
        else:
            srch = ""
        print(box_row(f"{D}Page {pgn}  ·  {len(buf)} results{srch}{spin}{R}", w))
        print()
        for i, v in enumerate(pg, 1):
            tmax  = w - 14
            title = v["title"][:tmax-1]+"…" if len(v["title"]) > tmax else v["title"]
            ch    = v["channel"][:25]
            print(f"  {Y2}{B}[{i:>2}]{R}  {W2}{B}{title}{R}")
            print(f"       {K2}{D}└ {ch}{R}")
            print()

    # ─── footer ───────────────────────────────────────────────────────
    print(box_mid(w))
    if MODE == "personal":
        print(box_row(f"{D}s=Subs  f=ForYou  l=Liked  h=Trending  p=Playlist  r=Refresh  q=Quit{R}", w))
    else:
        print(box_row(f"{D}f=ForYou  h=Trending  p=Playlist  r=Refresh  q=Quit{R}", w))
    print(box_row(f"{D}1–10 play  11 next  /search  or paste YouTube URL{R}", w))
    print(box_bot(w))
    print()

# ── Playlist UI ─────────────────────────────────────────────────────────────────
def pl_menu(last_v=None):
    if not HAS_REC or not REC.db_exists():
        cls(); print(f"\n  {R2}Playlists need a saved profile.{R}"); time.sleep(1.5); return

    while True:
        w = TW(); pls = REC.get_playlists(); cls()
        print(box_top(w, "PLAYLISTS"))
        if pls:
            for i, pl in enumerate(pls, 1):
                print(box_row(f"  {Y2}{B}[{i}]{R}  {W2}{B}{pl['name']}{R}  {D}({pl['count']} tracks){R}", w))
        else:
            print(box_row(f"{D}No playlists yet{R}", w))
        print(box_mid(w))
        print(box_row(f"  {G2}{B}[n]{R}  New", w))
        if last_v:
            t = last_v["title"][:35] + "…" if len(last_v["title"]) > 35 else last_v["title"]
            print(box_row(f"  {G2}{B}[a]{R}  Add  {D}\"{t}\"{R}", w))
        print(box_row(f"  {G2}{B}[b]{R}  Back", w))
        print(box_bot(w))

        try: raw = input(f"  {B}› {R}").strip().lower()
        except (KeyboardInterrupt, EOFError): return
        if raw in ("b", ""): return
        if raw == "n":
            try: name = input("  Name: ").strip()
            except (KeyboardInterrupt, EOFError): continue
            if name: REC.create_playlist(name); print(f"  {G2}✓{R}"); time.sleep(0.5)
        elif raw == "a" and last_v:
            if not pls: print(f"  Create a playlist first"); time.sleep(1); continue
            for i, pl in enumerate(pls, 1): print(f"  [{i}] {pl['name']}")
            try: ch = input("  Add to › ").strip()
            except (KeyboardInterrupt, EOFError): continue
            if ch.isdigit():
                idx = int(ch) - 1
                if 0 <= idx < len(pls):
                    REC.add_to_playlist(pls[idx]["id"], last_v)
                    print(f"  {G2}✓ Added{R}"); time.sleep(0.5)
        elif raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(pls): _pl_view(pls[idx])

def _pl_view(pl):
    while True:
        w = TW(); vids = REC.get_playlist_videos(pl["id"]); cls()
        print(box_top(w, f"🎵 {pl['name']}"))
        if vids:
            for i, v in enumerate(vids, 1):
                t = v["title"][:w-12]+"…" if len(v["title"]) > w-12 else v["title"]
                print(box_row(f"  {D}{i:>2}.{R}  {W2}{t}{R}", w))
                print(box_row(f"       {D}└ {v['channel']}{R}", w))
        else:
            print(box_row(f"{D}Empty{R}", w))
        print(box_mid(w))
        print(box_row(f"  {G2}{B}[▶]{R} Play   {G2}{B}[d]{R} Delete   {G2}{B}[b]{R} Back", w))
        print(box_bot(w))
        try: raw = input(f"  {B}› {R}").strip().lower()
        except (KeyboardInterrupt, EOFError): return
        if raw == "b": return
        if raw in ("p", "play", "▶"):
            if not vids: time.sleep(1); continue
            q = list(vids)
            more = fetch_related(q[-1]["id"], 5)
            seen = {v["id"] for v in q}
            for v in more:
                if v["id"] not in seen: q.append(v); seen.add(v["id"])
            play(q, 0, f"pl:{pl['name']}"); return
        if raw == "d":
            try: ok = input(f"  Delete '{pl['name']}'? [y/N]: ").strip().lower()
            except (KeyboardInterrupt, EOFError): continue
            if ok == "y": REC.delete_playlist(pl["id"]); return

# ── Startup ─────────────────────────────────────────────────────────────────────
def startup():
    global CURRENT_USER, MODE
    cls()
    w     = TW()
    users = REC.list_users() if HAS_REC else []

    print(box_top(w, "ytterm"))
    print(box_row(f"  {D}Your personal YouTube, in the terminal{R}", w))
    print(box_mid(w))

    if users:
        print(box_row(f"  {B}Profiles:{R}", w))
        for i, u in enumerate(users, 1):
            p    = REC.get_play_count(u) if HAS_REC else 0
            wm   = REC.get_warmth(u) if HAS_REC else "cold"
            icon = {"cold": "❄", "warm": "～", "hot": "🔥"}.get(wm, "·")
            mhint= "personal" if os.path.exists(os.path.join(SD, f"token_{u}.enc")) else "fresh"
            print(box_row(f"  {Y2}{B}[{i}]{R}  {W2}{B}{u}{R}  {D}{icon} {p} plays · {mhint}{R}", w))
        print(box_mid(w))
        print(box_row(f"  {G2}{B}[n]{R}  New profile", w))
        print(box_row(f"  {G2}{B}[a]{R}  Anonymous (temp)", w))
        print(box_bot(w))
        while True:
            try: raw = input(f"  {B}› {R}").strip()
            except (KeyboardInterrupt, EOFError): print(f"\n{D}Bye!{R}"); sys.exit(0)
            if raw.lower() == "a":
                CURRENT_USER = "__anon__"; MODE = "fresh"
                if HAS_REC: REC.set_user("__anon__"); REC.init_db()
                return
            if raw.lower() == "n": _new_profile(); return
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(users):
                    CURRENT_USER = users[idx]
                    if HAS_REC: REC.set_user(CURRENT_USER)
                    MODE = "personal" if os.path.exists(_tok_path()) else "fresh"
                    return
            print(f"  ⚠  Enter a number, n, or a")
    else:
        print(box_row(f"{D}No profiles yet{R}", w))
        print(box_mid(w))
        print(box_row(f"  {Y2}{B}[1]{R}  Create profile {D}— saves taste & playlists{R}", w))
        print(box_row(f"  {Y2}{B}[2]{R}  Anonymous      {D}— temp, resets on exit{R}", w))
        print(box_bot(w))
        while True:
            try: raw = input(f"  {B}[1/2]: {R}").strip()
            except (KeyboardInterrupt, EOFError): print(f"\n{D}Bye!{R}"); sys.exit(0)
            if raw == "1": _new_profile(); return
            if raw == "2":
                CURRENT_USER = "__anon__"; MODE = "fresh"
                if HAS_REC: REC.set_user("__anon__"); REC.init_db()
                return

def _new_profile():
    global CURRENT_USER, MODE
    while True:
        try: n = input(f"\n  {B}Profile name: {R}").strip()
        except (KeyboardInterrupt, EOFError): sys.exit(0)
        if n: CURRENT_USER = n.lower().replace(" ", "_"); break
    if HAS_REC: REC.set_user(CURRENT_USER); REC.init_db()

    w = TW(); cls()
    print(box_top(w, "Setup"))
    print(box_row(f"  Hi {B}{CURRENT_USER}{R}! Pick your mode  {D}(asked only once){R}", w))
    print(box_mid(w))
    print(box_row(f"  {Y2}{B}[1]{R}  Personal  — Google login, syncs subs & liked", w))
    print(box_row(f"  {Y2}{B}[2]{R}  Fresh     — no login, learns as you watch", w))
    print(box_bot(w))
    while True:
        try: raw = input(f"  {B}[1/2]: {R}").strip()
        except (KeyboardInterrupt, EOFError): sys.exit(0)
        if raw == "1": MODE = "personal"; return
        if raw == "2": MODE = "fresh";    return

# ── Search ─────────────────────────────────────────────────────────────────────
LAST_SEARCH = ""   # tracks the current search query for display in the feed

def load_search(query: str):
    """
    Search YouTube via yt-dlp and put the results into the foryou feed slot.
    Typing  /anything  in the main prompt triggers this.
    Results are shown with the search query displayed in the feed header.
    """
    global TAB, PAGE_OFFSET, LAST_SEARCH
    LAST_SEARCH = query
    print(f"  {D}Searching: {query}…{R}", flush=True)
    results = yt_search(query, 30)        # fetch up to 30 results
    FEED_BUF["foryou"] = results
    FEED_DONE["foryou"] = True
    FEED_SEEN.update(v["id"] for v in results)
    TAB = "foryou"
    PAGE_OFFSET = 0

# ── Main loop ──────────────────────────────────────────────────────────────────
def main_loop(youtube=None):
    global TAB, PAGE_OFFSET, _yt
    _yt = youtube

    cls()
    print(f"\n  {D}Loading…{R}", flush=True)
    if MODE == "fresh":
        TAB = "foryou"
        load_foryou(silent=True)
        threading.Thread(target=load_home, args=(True,), daemon=True).start()
    else:
        TAB = "subs"
        load_subs(youtube, silent=True)
        threading.Thread(target=load_home, args=(True,), daemon=True).start()

    PAGE_OFFSET = 0; last_v = None
    _start_pf(TAB)

    while True:
        render_feed()
        # auto-prefetch when near end
        buf = FEED_BUF[TAB]
        if (len(buf) - PAGE_OFFSET) // PER_PAGE <= PRE_AHEAD and not FEED_DONE[TAB]:
            _start_pf(TAB)

        while True:
            try: raw = input(f"  {B}› {R}").strip()
            except (KeyboardInterrupt, EOFError): print(f"\n{D}Bye!{R}"); sys.exit(0)

            rl = raw.lower()
            if rl == "q": print(f"\n{D}Bye!{R}"); sys.exit(0)
            if rl == "p": pl_menu(last_v=last_v); break
            if rl == "r":
                PAGE_OFFSET = 0
                {   "foryou": load_foryou,
                    "subs":   lambda: load_subs(youtube),
                    "liked":  lambda: load_liked(youtube),
                    "home":   load_home,
                }.get(TAB, load_foryou)()
                _start_pf(TAB); break
            if rl == "f":
                LAST_SEARCH = ""; TAB = "foryou"; PAGE_OFFSET = 0
                if not FEED_BUF["foryou"]: load_foryou()
                _start_pf(TAB); break
            if rl == "h":
                TAB = "home"; PAGE_OFFSET = 0
                if not FEED_BUF["home"]: load_home(); break
                break
            if MODE == "personal":
                if rl == "s":
                    TAB = "subs"; PAGE_OFFSET = 0
                    if not FEED_BUF["subs"]: load_subs(youtube)
                    _start_pf(TAB); break
                if rl == "l":
                    TAB = "liked"; PAGE_OFFSET = 0
                    if not FEED_BUF["liked"]: load_liked(youtube)
                    _start_pf(TAB); break

            vid = vid_id_from(raw)
            if vid:
                print(f"\n  {D}Fetching…{R}", flush=True)
                v = vid_info(vid); rel = fetch_related(vid, 5)
                last_v = v; play([v] + rel, 0, "url"); break

            # Search: type  /your query  to search YouTube directly
            if raw.startswith("/"):
                query = raw[1:].strip()
                if not query:
                    print(f"  {D}Type  /song name  to search{R}"); continue
                load_search(query); break

            if not raw.isdigit():
                print(f"  {D}1-10 play · 11 next · /search · tab keys · q quit{R}"); continue

            choice = int(raw)
            buf    = FEED_BUF[TAB]
            pg     = buf[PAGE_OFFSET: PAGE_OFFSET + PER_PAGE]

            if choice == 11:
                nxt = PAGE_OFFSET + PER_PAGE
                if nxt < len(buf):
                    PAGE_OFFSET = nxt; _start_pf(TAB); break
                elif not FEED_DONE[TAB]:
                    _wait_pf()
                    if nxt < len(buf):
                        PAGE_OFFSET = nxt; _start_pf(TAB); break
                    print(f"  {D}End of feed — press r{R}"); continue
                else:
                    print(f"  {D}End of feed — press r{R}"); continue

            if not pg: print(f"  {D}No videos — press r{R}"); continue
            if 1 <= choice <= len(pg):
                v = pg[choice - 1]; last_v = v
                print(f"\n  {D}Loading…{R}", flush=True)
                rel = fetch_related(v["id"], 5)
                play([v] + rel, 0, TAB); break
            else:
                print(f"  {D}1–{len(pg)} to play, 11 for next{R}")

# ── Entry ──────────────────────────────────────────────────────────────────────
def main():
    show_cur()
    for tool in ["yt-dlp", "mpv"]:
        if not shutil.which(tool):
            print(f"{R2}{tool} not found — run setup.py{R}"); sys.exit(1)

    startup()

    try:
        # Step 1: App lock — runs for ALL named profiles, before any cls().
        # Anonymous sessions return "" immediately and skip the prompt.
        pw = app_lock()

        # Step 2: Google OAuth — personal mode only.
        yt = None
        if MODE == "personal":
            try:
                from cryptography.fernet import Fernet
                from google_auth_oauthlib.flow import InstalledAppFlow
            except ImportError:
                print(f"{R2}Missing packages — run: python setup.py{R}"); sys.exit(1)
            yt = authenticate(pw)

        cls()
        w = TW()
        who  = "Anonymous" if CURRENT_USER == "__anon__" else CURRENT_USER
        mlbl = "Personal" if MODE == "personal" else "Fresh Start"
        print(box_top(w, "ytterm"))
        print(box_row(f"  {W2}{B}{who}{R}  {D}·  {mlbl}{R}", w))
        if HAS_REC and REC.db_exists():
            try:
                wm = REC.get_warmth(); p = REC.get_play_count()
                icon = {"cold": "❄", "warm": "～", "hot": "🔥"}.get(wm, "·")
                print(box_row(f"  {D}{icon} {wm} · {p} plays{R}", w))
            except Exception: pass
        print(box_bot(w)); print()

        main_loop(yt)

    finally:
        show_cur()
        if CURRENT_USER == "__anon__" and HAS_REC:
            try: os.remove(REC.get_db_path("__anon__"))
            except Exception: pass

if __name__ == "__main__":
    main()
