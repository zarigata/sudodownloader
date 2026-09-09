#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
☩ Suno Playlist Downloader ☩
by zarigata · https://zarigata.com

"Let everything that has breath praise the Lord. Praise the Lord!" — Psalm 150:6

Download every track from a public Suno playlist to your computer —
no account, no login, no download quota. Work faithfully, share generously,
and give thanks for every note. 🕊️

Works with any *public* playlist link, e.g.:
    https://suno.com/playlist/<your-playlist-id>

The tool only reads data that Suno already serves to anyone who opens
the playlist link (the same files the web player streams), paced politely
so it looks like a normal person listening to the playlist once.

Usage
-----
Interactive (just run it, then paste links):
    python3 suno_dl.py

Command line:
    python3 suno_dl.py <playlist-url> [more urls...] [options]
"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")

try:
    import requests
except ImportError:  # pragma: no cover
    print("Missing dependency: requests")
    print("Install it with:  pip install requests rich cryptography")
    sys.exit(3)

try:
    import base64
    import hashlib

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:  # pragma: no cover
    print("Missing dependency: cryptography")
    print("Install it with:  pip install requests rich cryptography")
    sys.exit(3)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover
    print("Missing dependency: rich")
    print("Install it with:  pip install requests rich")
    sys.exit(3)


__version__ = "1.2.0"

API_PLAYLIST = "https://studio-api.prod.suno.com/api/playlist/{pid}"
API_RIGHTS = "https://studio-api.prod.suno.com/api/mango/rights"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://suno.com",
}

CHUNK_SIZE = 64 * 1024
CONNECT_TIMEOUT, READ_TIMEOUT = 15, 60
MAX_RETRIES = 3
RETRY_DELAYS = (5, 15, 30)  # seconds, growing back-off

EXT_BY_TYPE = {
    "m4a-opus": ".m4a",
    "m4a": ".m4a",
    "mp3": ".mp3",
    "audio/mpeg": ".mp3",
    "wav": ".wav",
    "mp4": ".mp4",
}


class BlockedError(Exception):
    """Suno refused a request (403/429) — stop everything for safety."""


class PlaylistNotFoundError(Exception):
    """Playlist link is wrong or the playlist is not public."""


# --------------------------------------------------------------------------- #
#  Helpers — "whatever you do, work at it with all your heart" (Col 3:23) ✝
# --------------------------------------------------------------------------- #

def extract_playlist_id(text: str) -> str:
    """Pull the playlist id out of anything the user managed to paste."""
    text = text.strip()
    m = re.search(r"suno\.com/playlist/([0-9a-fA-F-]{30,40})", text)
    if m:
        return m.group(1).strip("/? ")
    m = re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                     r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text)
    if m:
        return text.lower()
    raise ValueError("not a Suno playlist link")


def sanitize_filename(name: str) -> str:
    """Make a string safe to use as a filename on macOS/Windows/Linux."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or "untitled"


def human_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "?"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def find_ffmpeg() -> str:
    return shutil.which("ffmpeg") or ""


# --------------------------------------------------------------------------- #
#  Suno API + downloads
# --------------------------------------------------------------------------- #

def make_session(proxy: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    return s


def _get_json(session: requests.Session, url: str, referer: str) -> dict:
    headers = {"Referer": referer}
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            if r.status_code in (403, 429):
                raise BlockedError(f"HTTP {r.status_code} while contacting Suno")
            if r.status_code == 404:
                raise PlaylistNotFoundError(url)
            r.raise_for_status()
            return r.json()
        except BlockedError:
            raise
        except PlaylistNotFoundError:
            raise
        except (requests.RequestException, ValueError) as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
    raise RuntimeError(f"request failed after {MAX_RETRIES} tries: {last_err}")


def fetch_playlist(session: requests.Session, pid: str) -> dict:
    """Fetch playlist metadata + all clips (follows pagination if ever needed)."""
    referer = f"https://suno.com/playlist/{pid}"
    entries: list = []  # (position, clip) — position = playlist's own order
    seen: set = set()
    meta: dict = {}
    page = 1
    while page <= 20:
        data = _get_json(session, API_PLAYLIST.format(pid=pid) + f"?page={page}", referer)
        meta = {k: v for k, v in data.items() if not isinstance(v, list)}
        batch = data.get("playlist_clips") or []
        new = 0
        for item in batch:
            if not isinstance(item, dict):
                continue
            clip = item.get("clip")
            if isinstance(clip, dict) and clip.get("id") and clip["id"] not in seen:
                seen.add(clip["id"])
                pos = item.get("relative_index")
                if not isinstance(pos, (int, float)):
                    pos = len(entries) + 1
                entries.append((pos, clip))
                new += 1
        total = data.get("num_total_results") or len(entries)
        if new == 0 or len(entries) >= total:
            break
        page += 1
    entries.sort(key=lambda pc: pc[0])  # keep the playlist's own track order
    meta["clips"] = [clip for _, clip in entries]
    return meta


def pick_media(clip: dict):
    """Return (url, extension) for the best progressive audio of a clip."""
    for m in clip.get("media_urls") or []:
        url = m.get("url")
        if not url:
            continue
        ctype = (m.get("content_type") or "").lower()
        delivery = (m.get("delivery") or "").lower()
        if delivery and delivery != "progressive":
            continue
        if ctype.startswith("video") or ctype == "mp4":
            continue  # video track — audio is what we want
        ext = EXT_BY_TYPE.get(ctype)
        if ext is None:
            ext = Path(url.split("?")[0]).suffix or ".m4a"
        return url, ext
    return None, None


# --------------------------------------------------------------------------- #
#  Content decryption ("Mango" scheme used by suno.com's own web player)
#  1. POST /api/mango/rights  →  {key, iv, glt}   (anonymous, per clip)
#  2. user key  = SHA-256(glt)
#  3. key/iv arrive AES-GCM-wrapped: nonce=bytes[0:12], tag=bytes[-16:],
#     additional data = the clip id string
#  4. the audio file itself is AES-CTR-128 with the unwrapped key/counter
#  ✝ "Ask, and it will be given to you" (Mt 7:7) — the keys are granted
#     freely to any guest listener; we ask politely, one clip at a time.
# --------------------------------------------------------------------------- #

def fetch_rights(session: requests.Session, clip_id: str) -> dict:
    """Ask Suno's rights server for this clip's wrapped key + guest token."""
    body = {"content_params": {"content_id": clip_id, "content_type": "clip"}}
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.post(API_RIGHTS, json=body,
                             timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            if r.status_code in (403, 429):
                raise BlockedError(f"HTTP {r.status_code} from the rights server")
            r.raise_for_status()
            data = r.json()
            if not (data.get("key") and data.get("iv") and data.get("glt")):
                raise RuntimeError("rights response incomplete")
            return data
        except BlockedError:
            raise
        except (requests.RequestException, ValueError) as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
    raise RuntimeError(f"rights request failed after {MAX_RETRIES} tries: {last_err}")


def _unwrap(wrapped_b64: str, clip_id: str, user_key: bytes) -> bytes:
    w = base64.b64decode(wrapped_b64)
    nonce, ct, tag = w[:12], w[12:-16], w[-16:]
    dec = Cipher(algorithms.AES(user_key), modes.GCM(nonce, tag)).decryptor()
    dec.authenticate_additional_data(clip_id.encode())
    return dec.update(ct) + dec.finalize()


def decrypt_bytes(data: bytes, clip_id: str, rights: dict) -> bytes:
    """Decrypt one downloaded (encrypted) audio file to playable bytes."""
    user_key = hashlib.sha256(rights["glt"].encode()).digest()
    key = _unwrap(rights["key"], clip_id, user_key)
    counter = _unwrap(rights["iv"], clip_id, user_key)
    dec = Cipher(algorithms.AES(key), modes.CTR(counter)).decryptor()
    return dec.update(data) + dec.finalize()


def is_playable_audio(path: Path) -> bool:
    """Cheap check: does the file start with a real MP4/WebM header?"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    if head[4:8] == b"ftyp":
        return True  # mp4 / m4a
    return head[:4] == bytes([0x1A, 0x45, 0xDF, 0xA3])  # webm


def download_file(session: requests.Session, url: str, dest: Path,
                  referer: str, progress: Progress, task_id) -> Path:
    """Stream one file to disk with retries; returns the final path."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    headers = {"Referer": referer}
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with session.get(url, headers=headers, stream=True,
                             timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
                if r.status_code in (403, 429):
                    raise BlockedError(f"HTTP {r.status_code} from Suno's file server")
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0) or None
                progress.update(task_id, total=total, completed=0)
                done = 0
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        if chunk:
                            fh.write(chunk)
                            done += len(chunk)
                            progress.update(task_id, completed=done)
            tmp.replace(dest)
            return dest
        except BlockedError:
            raise
        except requests.RequestException as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
    raise RuntimeError(f"download failed after {MAX_RETRIES} tries: {last_err}")


def transcode_mp3(src: Path) -> Path:
    """Convert a downloaded file to .mp3 in place (requires ffmpeg)."""
    dst = src.with_suffix(".mp3")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-codec:a", "libmp3lame", "-q:a", "2", str(dst)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(proc.stderr.decode(errors="replace").strip()[:200])
    src.unlink(missing_ok=True)
    return dst


# --------------------------------------------------------------------------- #
#  UI
# --------------------------------------------------------------------------- #

def banner(console: Console) -> None:
    console.print(Panel.fit(
        f"[bold]☩ Suno Playlist Downloader ☩[/]  v{__version__}\n"
        "[dim]Download every track from a public Suno playlist — "
        "no account, no quota.[/]\n"
        "[bright_black]by zarigata · zarigata.com · "
        "«Glory to Jesus Christ!»[/]",
        border_style="bright_magenta"))


def ask_urls(console: Console) -> list:
    """Interactive, paste-friendly URL entry."""
    console.print(
        "Paste one or more [bold cyan]public playlist links[/] "
        "(they look like [dim]https://suno.com/playlist/…[/]).\n"
        "Press [bold]Enter on an empty line[/] to start downloading.\n")
    urls = []
    while True:
        try:
            line = console.input("[bold cyan]  playlist link> [/]")
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip():
            if urls:
                break
            console.print("[yellow]Nothing added yet — paste a link first.[/]")
            continue
        try:
            pid = extract_playlist_id(line)
        except ValueError:
            console.print("[red]  ✗ that doesn't look like a Suno playlist link[/]")
            continue
        if pid not in urls:
            urls.append(pid)
            console.print(f"[green]  ✓ added[/] [dim]{pid}[/]")
    return urls


def summary_table(results: list, playlist_name: str) -> Table:
    table = Table(title=f"{playlist_name} — results", header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Track", overflow="ellipsis", max_width=46)
    table.add_column("Status")
    table.add_column("Size", justify="right")
    for row in results:
        num, title, status, size = row
        style = {"downloaded": "green", "converted": "green", "decrypted": "green",
                 "skipped": "dim", "failed": "red", "unavailable": "yellow"}.get(status, "")
        table.add_row(str(num), Text(title), Text(status, style=style), size)
    return table


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #

def process_playlist(pid: str, args, console: Console) -> int:
    """Fetch + download one playlist. Returns process exit code."""
    with console.status("Fetching playlist info…"):
        try:
            meta = fetch_playlist(make_session(args.proxy), pid)
        except PlaylistNotFoundError:
            console.print(Panel(
                "[red]Playlist not found.[/] Check that the link is correct and "
                "the playlist is [bold]public[/] (in Suno: playlist → ⋯ → Make Public).",
                title=f"suno.com/playlist/{pid}"))
            return 1
        except BlockedError:
            _print_blocked(console)
            return 2

    clips = meta.get("clips") or []
    name = sanitize_filename(meta.get("name") or pid)
    byline = (meta.get("user_display_name") or "").strip()
    handle = (meta.get("user_handle") or "").strip()
    if handle and not handle.startswith("@"):
        handle = "@" + handle

    if not clips:
        console.print(f"[yellow]Playlist '{name}' is empty — nothing to download.[/]")
        return 0

    outdir = Path(args.out) if args.out else Path("suno_downloads") / name
    outdir.mkdir(parents=True, exist_ok=True)

    console.print()
    console.print(f"  [bold]{name}[/] "
                  f"[dim]by {byline} {handle} — {len(clips)} tracks, "
                  f"{human_duration(meta.get('total_duration'))} total[/]")
    console.print(f"  saving to  [underline]{outdir.resolve()}[/]\n")

    ffmpeg = find_ffmpeg()
    if args.mp3 and not ffmpeg:
        console.print("[yellow]--mp3 needs ffmpeg, which wasn't found — "
                      "keeping original audio files instead.[/]\n")

    delay_base = max(0.0, args.delay)
    results = []
    failed = 0
    width = len(str(len(clips)))

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    overall = progress.add_task("[bold]overall[/]", total=len(clips))

    blocked = False
    with progress:
        for i, clip in enumerate(clips, 1):
            title = (clip.get("title") or clip.get("id") or "untitled").strip()
            url, ext = pick_media(clip)
            num = str(i).zfill(width)
            if not url:
                progress.advance(overall)
                results.append((num, title, "unavailable", "—"))
                console.print(f"  [yellow]⚠ {num} — {title}: no audio file "
                              "available (skipped)[/]")
                continue

            dest = outdir / f"{num} - {sanitize_filename(title)}{ext}"
            if dest.exists() and not args.overwrite:
                if is_playable_audio(dest):
                    progress.advance(overall)
                    size = human_size(dest.stat().st_size)
                    results.append((num, title, "skipped", size))
                    continue
                # file exists but is still encrypted (e.g. interrupted run of
                # an older version) — decrypt it in place, no re-download
                desc = f"{num} · {title} (decrypting)"
                fix_task = progress.add_task(desc[:52], total=None)
                try:
                    rights = fetch_rights(make_session(args.proxy), clip.get("id"))
                    blob = dest.read_bytes()
                    dest.write_bytes(decrypt_bytes(blob, clip.get("id"), rights))
                    if not is_playable_audio(dest):
                        raise RuntimeError("decrypted file still invalid")
                    size = human_size(dest.stat().st_size)
                    results.append((num, title, "decrypted", size))
                except (BlockedError, RuntimeError, ValueError) as e:
                    if isinstance(e, BlockedError):
                        progress.remove_task(fix_task)
                        blocked = True
                        break
                    failed += 1
                    results.append((num, title, "failed", "—"))
                    console.print(f"\n[red]✗ {num} — {title}: {e}[/]")
                finally:
                    try:
                        progress.remove_task(fix_task)
                    except Exception:
                        pass
                    progress.advance(overall)
                if i < len(clips):
                    time.sleep(delay_base + random.uniform(0, delay_base))
                continue

            desc = f"{num} · {title}"
            if len(desc) > 52:
                desc = desc[:49] + "…"
            file_task = progress.add_task(desc, total=None)
            try:
                download_file(make_session(args.proxy), url, dest,
                              f"https://suno.com/playlist/{pid}",
                              progress, file_task)
                # the file arrives encrypted — get the keys and decrypt it
                rights = fetch_rights(make_session(args.proxy), clip.get("id"))
                blob = dest.read_bytes()
                dest.write_bytes(decrypt_bytes(blob, clip.get("id"), rights))
                if not is_playable_audio(dest):
                    raise RuntimeError("decrypted file has no audio header")
                status = "downloaded"
                if args.mp3 and ffmpeg:
                    try:
                        dest = transcode_mp3(dest)
                        status = "converted"
                    except RuntimeError as e:
                        console.print(f"\n[yellow]mp3 conversion failed for "
                                      f"'{title}': {e}[/]")
                size = human_size(dest.stat().st_size)
                results.append((num, title, status, size))
            except BlockedError:
                progress.remove_task(file_task)
                blocked = True
                break
            except RuntimeError as e:
                failed += 1
                results.append((num, title, "failed", "—"))
                console.print(f"\n[red]✗ {num} — {title}: {e}[/]")
            finally:
                try:
                    progress.remove_task(file_task)
                except Exception:
                    pass
                progress.advance(overall)
            # polite pause between tracks (randomised, like a person browsing)
            # ✝ "Anyone who is patient has great understanding" (Prov 14:29)
            if i < len(clips):
                time.sleep(delay_base + random.uniform(0, delay_base))

    console.print()
    console.print(summary_table(results, name))
    downloaded = sum(1 for r in results if r[2] in ("downloaded", "converted", "decrypted"))
    skipped = sum(1 for r in results if r[2] == "skipped")
    console.print(
        f"\n  [green]{downloaded} downloaded[/] · [dim]{skipped} already on disk[/]"
        f" · [red]{failed} failed[/]   →  [underline]{outdir.resolve()}[/]")
    console.print(
        "  [bright_black]Δόξα τῷ Θεῷ πάντων ἕνεκεν — Glory to God for all things! 🕊️[/]\n")
    if blocked:
        _print_blocked(console)
        return 2
    return 1 if failed else 0


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def _print_blocked(console: Console) -> None:
    # ✝ patience is a virtue — when the door closes, we do not bang on it
    console.print(Panel(
        "[bold red]Suno refused the connection (403/429).[/]\n"
        "The tool stopped immediately to keep your connection safe.\n"
        "Wait a few hours and try again, run with fewer links at a time, or "
        "route through your VPN:\n"
        "[dim]    python3 suno_dl.py <link> --proxy socks5://127.0.0.1:1080[/]",
        title="Taking a break is safest", border_style="red"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="suno_dl",
        description="Download every track from public Suno playlists — "
                    "no account needed.",
        epilog="Glory to Jesus Christ! 🕊️  by zarigata — https://zarigata.com")
    parser.add_argument("urls", nargs="*",
                        help="playlist link(s) or id(s)")
    parser.add_argument("--out", metavar="DIR",
                        help="output folder (default: suno_downloads/<playlist>)")
    parser.add_argument("--delay", type=float, default=2.0, metavar="SEC",
                        help="base pause between downloads in seconds "
                             "(randomly 1–2× this; default 2)")
    parser.add_argument("--overwrite", action="store_true",
                        help="re-download tracks that already exist on disk")
    parser.add_argument("--proxy", metavar="URL",
                        help="route all traffic through a proxy/VPN, e.g. "
                             "socks5://127.0.0.1:1080 or http://host:8080")
    parser.add_argument("--mp3", action="store_true",
                        help="also convert downloads to .mp3 (needs ffmpeg; "
                             "originals are .m4a)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    console = Console()
    banner(console)

    pids = []
    for u in args.urls:
        try:
            pids.append(extract_playlist_id(u))
        except ValueError:
            console.print(f"[red]'{u}' is not a Suno playlist link — skipping.[/]")
    if not pids:
        pids = ask_urls(console)
    if not pids:
        console.print("[dim]No playlists given — bye![/]")
        return 0

    worst = 0
    for n, pid in enumerate(pids):
        if n:
            console.print("\n[dim]— next playlist —[/]\n")
        worst = max(worst, process_playlist(pid, args, console))

    # keep the window open when launched by double-click (skip for scripts/tests)
    if not os.environ.get("SUNODL_NO_PAUSE") and (
            not args.urls or not sys.stdin.isatty()):
        try:
            input("\nPress Enter to close…")
        except (EOFError, KeyboardInterrupt):
            pass
    return worst


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted — already-finished tracks are kept on disk. Bye!")
        sys.exit(130)
