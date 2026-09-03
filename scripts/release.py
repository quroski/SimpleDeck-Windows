#!/usr/bin/env python3
"""=============================================================================
  Simple Deck - Release CLI
  ----------------------------------------------------------------------------
  Buduje instalatory (.exe + .msi), tworzy git tag i publikuje GitHub Release.

  Wymagania:
    - git  +  gh CLI (gh auth login)  ... tag + release
    - Windows 10/11  +  build.ps1     ... budowanie instalatorow

  Uzycie:
    python scripts/release.py                    # Windows: build + tag + release
    python scripts/release.py --skip-build       # tag + release (pre-built artefakty)
    python scripts/release.py --ci               # tag + push tylko; CI buduje i releasu
    python scripts/release.py --version 1.1.0    # nadpisz wersje
    python scripts/release.py --prerelease       # oznacz jako pre-release
    python scripts/release.py --dry-run          # podglad, bez zmian
    python scripts/release.py --notes FILE       # wlasne release notes z pliku
    python scripts/release.py --repo OWNER/NAME  # nadpisz wykryte repo

  Strumien:
    1. Czytaj wersje z desktop/pyproject.toml
    2. (Windows) Zbuduj przez installer/windows/build.ps1 -> output/*.exe + *.msi
    3. Zweryfikuj artefakty
    4. git tag v{version} + git push origin v{version}
    5. gh release create z artefaktami + auto-notes
  ==========================================================================="""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# --- Sciezki projektu (relatywnie do tego pliku) ---------------------------
ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT  # W nowym repo desktop jest w root
INSTALLER = ROOT / "installer" / "windows"
OUTPUT_DIR = INSTALLER / "output"
PYPROJECT = ROOT / "pyproject.toml"

STEPS = 4  # uzywane do numerowania [N/4]


# === Pomocnicze ============================================================

def read_version() -> str:
    """Wczytaj wersje z desktop/pyproject.toml."""
    text = PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r'^\s*version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if m is None:
        die(f"Nie znaleziono wersji w {PYPROJECT}")
        raise AssertionError("unreachable")  # die() zawsze podnosi SystemExit
    return m.group(1)


def die(msg: str, code: int = 1) -> None:
    print(f"\n  BLAD: {msg}", file=sys.stderr)
    raise SystemExit(code)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Uruchom komende, wyjdz przy bledzie."""
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        die(f"Komenda nie powiodla sie (exit {r.returncode})", r.returncode)
    return r


def run_quiet(cmd: list[str]) -> subprocess.CompletedProcess:
    """Uruchom komende, przechwyc output, nie rzuce przy bledzie."""
    return subprocess.run(cmd, capture_output=True, text=True)


def is_windows() -> bool:
    return sys.platform == "win32"


def check_tool(name: str) -> bool:
    return shutil.which(name) is not None


def get_repo(args_repo: str | None) -> str:
    """Pobierz owner/name repozytorium z gh lub argumentu."""
    if args_repo:
        return args_repo
    r = run_quiet(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    # fallback: sprobuj z git remote
    r2 = run_quiet(["git", "remote", "get-url", "origin"])
    if r2.returncode == 0:
        url = r2.stdout.strip()
        # git@github.com:OWNER/NAME.git  lub  https://github.com/OWNER/NAME.git
        m = re.search(r'github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$', url)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    die("Nie mozna okreslic repozytorium. Uzyj --repo OWNER/NAME.")
    raise AssertionError("unreachable")  # die() zawsze podnosi SystemExit


# === Krok 1: Build =========================================================

def build_installers(version: str, dry_run: bool) -> list[Path]:
    """Uruchom build.ps1 na Windows; zwroc liste artefaktow."""
    print(f"\n[{1}/{STEPS}] Budowanie instalatorow (Windows)...")

    build_ps1 = INSTALLER / "build.ps1"
    if not build_ps1.exists():
        die(f"Brak skryptu buildu: {build_ps1}")

    if not is_windows():
        die(
            "Build wymaga Windows (PyInstaller + Inno Setup + WiX).\n"
            "  Opcje:\n"
            "    - Zbuduj na Windows i uzyc --skip-build\n"
            "    - Uzyj --ci (CI zbuduje w chmurze)"
        )

    if not check_tool("powershell"):
        die("Brak 'powershell' w PATH.")

    if dry_run:
        print("  (dry-run) Wywolano by: powershell -File build.ps1")
        return find_artifacts(version)

    run([
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(build_ps1),
    ])

    artifacts = find_artifacts(version)
    if not artifacts:
        die(f"Brak artefaktow po buildzie w {OUTPUT_DIR}")
    _report_artifacts(artifacts)
    return artifacts


def find_artifacts(version: str) -> list[Path]:
    """Znajdz pre-built artefakty w output/."""
    names = [
        f"Simple-Deck-Setup-{version}.exe",
        f"Simple-Deck-{version}.msi",
    ]
    found = []
    for n in names:
        p = OUTPUT_DIR / n
        if p.exists():
            found.append(p)
    return found


def _report_artifacts(artifacts: list[Path]) -> None:
    for a in artifacts:
        mb = a.stat().st_size / (1024 * 1024)
        print(f"    OK  {a.name}  ({mb:.1f} MB)")


# === Krok 2: Tag ===========================================================

def create_tag(version: str, repo: str, dry_run: bool) -> str:
    """Utworz git tag v{version} i wypchnij."""
    del repo  # zarezerwowane dla przyszłego użycia (GitHub remote w krokach 3-4)
    tag = f"v{version}"
    print(f"\n[2/{STEPS}] Git tag {tag}...")

    # Czy tag juz istnieje lokalnie?
    r = run_quiet(["git", "tag", "-l", tag])
    if tag in r.stdout.split():
        print(f"    Tag {tag} istnieje lokalnie.")
    else:
        if dry_run:
            print(f"    (dry-run) Utworzono by: git tag {tag}")
        else:
            run(["git", "tag", tag])
            print(f"    Utworzono tag {tag}.")

    # Push taga
    if dry_run:
        print(f"    (dry-run) Wypchnieto by: git push origin {tag}")
    else:
        r2 = run_quiet(["git", "push", "origin", tag])
        if r2.returncode != 0:
            # Moze tag juz jest na remote - to OK
            print(f"    Push: {r2.stderr.strip() or '(moze juz istnieje na remote)'}")
        else:
            print(f"    Wypchnieto {tag} do origin.")

    return tag


# === Krok 3: Release =======================================================

def create_release(
    version: str, tag: str, repo: str,
    artifacts: list[Path], prerelease: bool,
    notes: str, dry_run: bool,
) -> None:
    """Utworz GitHub Release z artefaktami przez gh CLI."""
    title = f"Simple Deck v{version}"
    print(f"\n[3/{STEPS}] GitHub Release {tag} ({repo})...")

    # Sprawdz czy release juz istnieje
    r = run_quiet(["gh", "release", "view", tag, "--repo", repo, "--json", "url"])
    if r.returncode == 0:
        existing = json.loads(r.stdout).get("url", "")
        print(f"    Release juz istnieje: {existing}")
        if artifacts:
            print(f"    Upload artefaktow (--clobber zastapi stare)...")
            cmd = ["gh", "release", "upload", tag, "--repo", repo, "--clobber"]
            cmd += [str(a) for a in artifacts]
            if dry_run:
                print(f"    (dry-run) { ' '.join(cmd) }")
            else:
                run(cmd)
                _report_artifacts(artifacts)
        return

    # Nowy release
    cmd = ["gh", "release", "create", tag, "--repo", repo, "--title", title]
    if prerelease:
        cmd.append("--prerelease")
    if notes:
        cmd += ["--notes", notes]
    else:
        cmd.append("--generate-notes")
    cmd += [str(a) for a in artifacts]

    if dry_run:
        print(f"    (dry-run) { ' '.join(cmd) }")
        return

    run(cmd)
    _report_artifacts(artifacts)

    # Pobierz URL release'a
    r2 = run_quiet(["gh", "release", "view", tag, "--repo", repo, "--json", "url"])
    if r2.returncode == 0:
        url = json.loads(r2.stdout).get("url", "")
        if url:
            print(f"\n[{4}/{STEPS}] Gotowe! Release opublikowany:")
            print(f"    {url}")


# === Glowna logika =========================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build installers + git tag + GitHub release dla Simple Deck.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--version", help="Nadpisz wersje (domyslnie: z pyproject.toml)")
    ap.add_argument("--skip-build", action="store_true",
                    help="Pomin build; dolacz pre-built artefakty z output/")
    ap.add_argument("--ci", action="store_true",
                    help="Tylko tag + push; CI zbuduje i utworzy release")
    ap.add_argument("--prerelease", action="store_true",
                    help="Oznacz release jako pre-release")
    ap.add_argument("--dry-run", action="store_true",
                    help="Podglad bez wykonywania zmian")
    ap.add_argument("--notes", help="Tekst release notes")
    ap.add_argument("--notes-file", help="Plik z release notes")
    ap.add_argument("--repo", help="Nadpisz repo (OWNER/NAME)")
    args = ap.parse_args()

    # --- Walidacja narzedzi ---
    missing = [t for t in ("git", "gh") if not check_tool(t)]
    if missing:
        die(f"Brak narzedzi w PATH: {', '.join(missing)}")

    version = args.version or read_version()
    print(f"Wersja: {version}")
    print(f"Dry-run: {args.dry_run}")

    # --- Release notes ---
    notes = args.notes or ""
    if args.notes_file:
        notes = Path(args.notes_file).read_text(encoding="utf-8")

    repo = get_repo(args.repo)
    print(f"Repo: {repo}")

    # --- Tryb CI: tylko tag + push, reszta w CI ---
    if args.ci:
        tag = create_tag(version, repo, args.dry_run)
        print(f"\n[{4}/{STEPS}] Gotowe! Tag {tag} wypchniety.")
        print(f"    CI zbuduje instalatory i utworzy release automatycznie.")
        if not args.dry_run:
            print(f"    Obserwuj: https://github.com/{repo}/actions")
        return 0

    # --- Krok 1: Artefakty ---
    if args.skip_build:
        print(f"\n[{1}/{STEPS}] Pominito build (--skip-build). Szukam artefaktow...")
        artifacts = find_artifacts(version)
        if not artifacts:
            die(f"Brak artefaktow w {OUTPUT_DIR} dla wersji {version}.\n"
                "  Najpierw zbuduj na Windows: cd installer/windows && .\\build.ps1")
        _report_artifacts(artifacts)
    else:
        artifacts = build_installers(version, args.dry_run)

    # --- Krok 2: Tag ---
    tag = create_tag(version, repo, args.dry_run)

    # --- Krok 3: Release ---
    create_release(version, tag, repo, artifacts, args.prerelease, notes, args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
