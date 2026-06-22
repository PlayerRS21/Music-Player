#!/usr/bin/env python3
"""
setup.py — ytterm Cross-Platform Setup
───────────────────────────────────────
Detects your OS, checks dependencies, installs missing ones.
Run this first before ytterm_final.py.

Supports:
  • Linux (Arch, Ubuntu/Debian, Fedora, any with pip)
  • macOS
  • Windows
"""

import os
import sys
import platform
import subprocess
import shutil

# ── Colors (with Windows fallback) ───────────────────────────────────────────
IS_WIN = platform.system() == "Windows"

def _enable_win_colors():
    """Enable ANSI colors on Windows 10+."""
    if IS_WIN:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

_enable_win_colors()

R  = "\033[0m"
BO = "\033[1m"
GR = "\033[92m"
RE = "\033[91m"
YE = "\033[93m"
CY = "\033[96m"
DI = "\033[2m"

def ok(msg):  print(f"  {GR}✓{R}  {msg}")
def err(msg): print(f"  {RE}✗{R}  {msg}")
def warn(msg):print(f"  {YE}!{R}  {msg}")
def info(msg):print(f"  {CY}→{R}  {msg}")

# ── OS Detection ──────────────────────────────────────────────────────────────
def detect_os():
    system = platform.system()
    if system == "Linux":
        # detect distro
        try:
            with open("/etc/os-release") as f:
                lines = f.read()
            if "arch" in lines.lower() or "manjaro" in lines.lower() or "endeavour" in lines.lower():
                return "arch"
            elif "ubuntu" in lines.lower() or "debian" in lines.lower() or "mint" in lines.lower():
                return "debian"
            elif "fedora" in lines.lower() or "rhel" in lines.lower() or "centos" in lines.lower():
                return "fedora"
        except Exception:
            pass
        return "linux"
    elif system == "Darwin":
        return "macos"
    elif system == "Windows":
        return "windows"
    return "unknown"

# ── Helpers ───────────────────────────────────────────────────────────────────
def run(cmd, capture=True):
    """Run a shell command, return (success, output)."""
    try:
        r = subprocess.run(cmd, capture_output=capture, text=True, shell=isinstance(cmd, str))
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as e:
        return False, str(e)

def cmd_exists(name):
    return shutil.which(name) is not None

def ask(prompt, default="y"):
    try:
        ans = input(f"  {prompt} [{default.upper() if default=='y' else default}/{('n' if default=='y' else 'N')}]: ").strip().lower()
        return ans in ("y", "yes", "") if default == "y" else ans in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)

# ── Python package installer ──────────────────────────────────────────────────
REQUIRED_PACKAGES = [
    "cryptography",
    "google-auth",
    "google-auth-oauthlib",
    "google-api-python-client",
]

def get_pip():
    """Find the right pip to use — prefer venv pip."""
    sd = os.path.dirname(os.path.abspath(__file__))
    # check for local venv
    for pip_path in [
        os.path.join(sd, "env", "bin", "pip"),
        os.path.join(sd, "env", "Scripts", "pip.exe"),  # Windows venv
    ]:
        if os.path.exists(pip_path):
            return pip_path
    # fallback to system pip
    for name in ["pip3", "pip"]:
        if cmd_exists(name):
            return name
    return None

def check_python_packages():
    print(f"\n{BO}Checking Python packages…{R}")
    missing = []
    for pkg in REQUIRED_PACKAGES:
        # normalize package name for import check
        import_name = pkg.replace("-", "_").split("[")[0]
        # special cases
        if pkg == "google-auth-oauthlib":
            import_name = "google_auth_oauthlib"
        elif pkg == "google-api-python-client":
            import_name = "googleapiclient"
        elif pkg == "google-auth":
            import_name = "google.auth"

        try:
            __import__(import_name)
            ok(pkg)
        except ImportError:
            err(f"{pkg} — not installed")
            missing.append(pkg)

    if missing:
        pip = get_pip()
        if not pip:
            err("No pip found. Install pip first.")
            return False

        print(f"\n  Found pip: {DI}{pip}{R}")
        if ask(f"Install {len(missing)} missing package(s)?"):
            for pkg in missing:
                info(f"Installing {pkg}…")
                ok_, out = run([pip, "install", pkg])
                if ok_:
                    ok(f"{pkg} installed")
                else:
                    err(f"Failed to install {pkg}")
                    print(f"    {DI}{out[:200]}{R}")
                    return False
    return True

# ── yt-dlp checker ────────────────────────────────────────────────────────────
def check_ytdlp(os_type):
    print(f"\n{BO}Checking yt-dlp…{R}")

    if cmd_exists("yt-dlp"):
        ok("yt-dlp found")
        # check version
        ok_, out = run(["yt-dlp", "--version"])
        if ok_:
            print(f"    {DI}version: {out.strip()}{R}")
        return True

    err("yt-dlp not found")

    install_cmds = {
        "arch":    ["sudo pacman -S yt-dlp"],
        "debian":  ["sudo apt install yt-dlp"],
        "fedora":  ["sudo dnf install yt-dlp"],
        "linux":   ["pip install yt-dlp", "# or check https://github.com/yt-dlp/yt-dlp"],
        "macos":   ["brew install yt-dlp", "# or: pip install yt-dlp"],
        "windows": ["winget install yt-dlp", "# or download from https://github.com/yt-dlp/yt-dlp/releases"],
    }

    cmds = install_cmds.get(os_type, ["pip install yt-dlp"])
    print(f"\n  {YE}Install yt-dlp with:{R}")
    for cmd in cmds:
        print(f"    {BO}{cmd}{R}")

    # auto install for linux if pacman/apt available
    if os_type == "arch" and cmd_exists("pacman"):
        if ask("Auto-install yt-dlp via pacman?"):
            ok_, out = run("sudo pacman -S --noconfirm yt-dlp", capture=False)
            return ok_

    elif os_type == "debian" and cmd_exists("apt"):
        if ask("Auto-install yt-dlp via apt?"):
            ok_, out = run("sudo apt install -y yt-dlp", capture=False)
            return ok_

    elif os_type == "macos" and cmd_exists("brew"):
        if ask("Auto-install yt-dlp via brew?"):
            ok_, out = run("brew install yt-dlp", capture=False)
            return ok_

    elif os_type == "windows":
        if cmd_exists("winget"):
            if ask("Auto-install yt-dlp via winget?"):
                ok_, out = run("winget install yt-dlp", capture=False)
                return ok_
        else:
            # pip fallback on windows
            pip = get_pip()
            if pip and ask("Install yt-dlp via pip?"):
                ok_, out = run([pip, "install", "yt-dlp"])
                return ok_

    # try pip as last resort
    pip = get_pip()
    if pip and ask("Try installing yt-dlp via pip?"):
        ok_, out = run([pip, "install", "yt-dlp"])
        if ok_:
            ok("yt-dlp installed via pip")
            return True

    return False

# ── mpv checker ───────────────────────────────────────────────────────────────
def check_mpv(os_type):
    print(f"\n{BO}Checking mpv…{R}")

    if cmd_exists("mpv"):
        ok("mpv found")
        return True

    err("mpv not found")

    install_cmds = {
        "arch":    "sudo pacman -S mpv",
        "debian":  "sudo apt install mpv",
        "fedora":  "sudo dnf install mpv",
        "macos":   "brew install mpv",
        "windows": "winget install mpv  # or download from https://mpv.io",
        "linux":   "Install mpv from your distro's package manager",
    }

    print(f"\n  {YE}Install mpv with:{R}")
    print(f"    {BO}{install_cmds.get(os_type, 'install mpv')}{R}")

    if os_type == "arch" and cmd_exists("pacman"):
        if ask("Auto-install mpv via pacman?"):
            run("sudo pacman -S --noconfirm mpv", capture=False)
            return cmd_exists("mpv")

    elif os_type == "debian" and cmd_exists("apt"):
        if ask("Auto-install mpv via apt?"):
            run("sudo apt install -y mpv", capture=False)
            return cmd_exists("mpv")

    elif os_type == "macos" and cmd_exists("brew"):
        if ask("Auto-install mpv via brew?"):
            run("brew install mpv", capture=False)
            return cmd_exists("mpv")

    elif os_type == "windows":
        if cmd_exists("winget") and ask("Auto-install mpv via winget?"):
            run("winget install mpv", capture=False)
            return cmd_exists("mpv")

    return False

# ── client_secret.json checker ────────────────────────────────────────────────
def check_client_secret():
    print(f"\n{BO}Checking Google API credentials…{R}")
    sd     = os.path.dirname(os.path.abspath(__file__))
    fpath  = os.path.join(sd, "client_secret.json")
    if os.path.exists(fpath):
        ok("client_secret.json found")
        return True
    err("client_secret.json not found")
    print(f"""
  {YE}You need a Google OAuth2 client secret to use the Subscriptions and Liked tabs.{R}
  
  Steps:
    1. Go to https://console.cloud.google.com
    2. Create a project (or select existing)
    3. Enable "YouTube Data API v3"
    4. Go to Credentials → Create Credentials → OAuth Client ID
    5. Application type: Desktop app
    6. Download JSON → rename to client_secret.json
    7. Place it in: {DI}{sd}{R}
    8. Go to OAuth consent screen → Add your email as a test user
""")
    return False

# ── Virtual env check ─────────────────────────────────────────────────────────
def check_venv():
    print(f"\n{BO}Checking virtual environment…{R}")
    sd     = os.path.dirname(os.path.abspath(__file__))
    # check common venv names
    for venv_name in ["env", ".venv", "venv"]:
        venv_path = os.path.join(sd, venv_name)
        if os.path.exists(venv_path):
            ok(f"Virtual env found: {venv_name}/")
            return True

    warn("No virtual environment found")
    if ask("Create a virtual environment (env/)?"):
        ok_, out = run([sys.executable, "-m", "venv", os.path.join(sd, "env")])
        if ok_:
            ok("Virtual environment created: env/")
            return True
        else:
            err("Failed to create venv")
            print(f"    {DI}{out[:200]}{R}")
    return False

# ── Takeout check ─────────────────────────────────────────────────────────────
def _list_profiles(sd):
    """Return list of existing profile names by scanning for taste_*.db files."""
    profiles = []
    for f in os.listdir(sd):
        if f.startswith("taste_") and f.endswith(".db"):
            name = f[6:-3]   # strip  taste_  and  .db
            if name != "__anon__":
                profiles.append(name)
    return sorted(profiles)

def _pick_profile_for_import(sd):
    """
    Ask the user which profile to import Takeout into.
    Shows existing profiles as numbered options.
    User can pick an existing one or type a new name.
    Returns the chosen profile name string.
    """
    existing = _list_profiles(sd)
    print()
    if existing:
        print(f"  {BO}Existing profiles:{R}")
        for i, name in enumerate(existing, 1):
            db = os.path.join(sd, f"taste_{name}.db")
            kb = os.path.getsize(db) // 1024 if os.path.exists(db) else 0
            print(f"    [{i}] {name}  ({kb} KB)")
        print(f"    [n] Create a new profile")
    else:
        print(f"  {DI}No profiles yet — you can create one now.{R}")
        print(f"    [n] Create a new profile")
    print()

    while True:
        try:
            raw = input(f"  {BO}Import into profile [1/{len(existing) if existing else 'n'}…/n]: {R}").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return None    # user cancelled

        if raw.lower() == "n":
            # Ask for a new profile name
            while True:
                try:
                    name = input("  New profile name: ").strip()
                except (KeyboardInterrupt, EOFError):
                    print()
                    return None
                # Sanitise: lowercase, spaces → underscores, no special chars
                name = name.lower().replace(" ", "_")
                name = "".join(c for c in name if c.isalnum() or c == "_")
                if name:
                    return name
                print("  Name cannot be empty.")

        if raw.isdigit():
            idx = int(raw) - 1
            if existing and 0 <= idx < len(existing):
                return existing[idx]

        print(f"  Enter a number (1–{len(existing)}) or 'n'.")


def check_takeout():
    print(f"\n{BO}Checking Google Takeout data…{R}")
    sd = os.path.dirname(os.path.abspath(__file__))

    # Check if ANY per-user taste DB already exists (not the old taste.db)
    profiles = _list_profiles(sd)
    if profiles:
        ok(f"Profile DBs found: {', '.join(profiles)}")
        # Still offer to import more / into another profile
        takeout_path = os.path.join(sd, "Takeout")
        if os.path.exists(takeout_path) and ask("Import Takeout into a profile?"):
            _run_import(sd, takeout_path)
        return

    # No profiles yet — look for a Takeout folder
    takeout_path = os.path.join(sd, "Takeout")
    if os.path.exists(takeout_path):
        warn("Takeout folder found but no profiles built yet")
        if ask("Import Takeout data now?"):
            _run_import(sd, takeout_path)
    else:
        warn("No Takeout data found — For You tab will start cold")
        print(f"""
  {DI}To enable personalised recommendations:
    1. Go to takeout.google.com
    2. Select only "YouTube and YouTube Music"
    3. Download and extract the zip into: {sd}
    4. Run:  python recommender.py --import ./Takeout
       (or re-run setup.py and it will offer to import){R}
""")


def _run_import(sd, takeout_path):
    """
    Pick a target profile interactively then call recommender.py
    with --import <path> --user <name> so it imports into exactly
    that profile's DB, not the default.
    """
    profile = _pick_profile_for_import(sd)
    if not profile:
        warn("Import cancelled.")
        return

    info(f"Importing into profile '{profile}'…")
    rec_path = os.path.join(sd, "recommender.py")

    # Pass --user explicitly so recommender never falls back to "default"
    ok_, _ = run(
        [sys.executable, rec_path, "--import", takeout_path, "--user", profile],
        capture=False
    )
    if ok_:
        ok(f"Takeout imported into profile '{profile}' — For You tab enabled!")
    else:
        err("Import failed. Run manually:")
        print(f"    python recommender.py --import ./Takeout --user {profile}")

# ── Summary ───────────────────────────────────────────────────────────────────
def print_summary(results):
    print(f"\n{BO}{CY}{'═'*50}{R}")
    print(f"{BO}{CY}  Setup Summary{R}")
    print(f"{BO}{CY}{'═'*50}{R}")
    all_ok = True
    for name, status in results.items():
        if status:
            ok(name)
        else:
            err(name)
            all_ok = False
    print(f"\n{BO}{CY}{'─'*50}{R}")
    if all_ok:
        print(f"  {GR}{BO}✅ All good! Run ytterm with:{R}")
        print(f"     {BO}python ytterm_final.py{R}")
    else:
        print(f"  {YE}Fix the issues above, then run ytterm_final.py{R}")
    print(f"{BO}{CY}{'═'*50}{R}\n")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{BO}{CY}ytterm Setup{R}")
    print(f"{DI}Cross-platform dependency checker & installer{R}\n")

    os_type = detect_os()
    os_name = {
        "arch": "Arch Linux", "debian": "Debian/Ubuntu",
        "fedora": "Fedora", "linux": "Linux",
        "macos": "macOS", "windows": "Windows", "unknown": "Unknown"
    }.get(os_type, os_type)

    print(f"  Detected OS: {BO}{os_name}{R}")
    print(f"  Python:      {BO}{sys.version.split()[0]}{R}")

    results = {}

    check_venv()
    results["Python packages"] = check_python_packages()
    results["yt-dlp"]          = check_ytdlp(os_type)
    results["mpv"]             = check_mpv(os_type)
    results["client_secret"]   = check_client_secret()
    check_takeout()

    print_summary(results)

if __name__ == "__main__":
    main()
