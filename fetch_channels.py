"""List the Telegram channels this session can read, ready for ``channels.json``.

A Telegram *user* session can only read channels the account is subscribed to,
so the reliable way to find a channel id is to enumerate the account's dialogs.
This script prints them in a copy/paste friendly table and can append new
entries to the channel file for you.

Examples::

    python fetch_channels.py                     # list every channel and group
    python fetch_channels.py --search gold       # filter title, @username or id
    python fetch_channels.py --only channel      # skip groups
    python fetch_channels.py --timezone-hints    # guess a timezone from posts
    python fetch_channels.py --write             # append new, disabled entries
    python fetch_channels.py --write --enable    # ... and enable them at once
    python fetch_channels.py --json > all.json   # machine-readable

If no authorized session exists yet, the interactive login from
``telegram_login.py`` runs first automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from telethon import TelegramClient, utils

from telegram_listener import DEFAULT_CHANNELS_FILE, load_channels
from telegram_login import ensure_login

TIMEZONE_PATTERN = re.compile(r"\b(?:GMT|UTC)\s*([+-])\s*(\d{1,2})(?::30)?\b", re.IGNORECASE)

EXAMPLES = """\
examples:
  python fetch_channels.py
  python fetch_channels.py --search gold --only channel
  python fetch_channels.py --timezone-hints
  python fetch_channels.py --write --enable
"""


def marked_id(entity: Any) -> str:
    """Return the ``-100...`` id that ``NewMessage(chats=...)`` expects."""
    try:
        peer_id = utils.get_peer_id(entity, add_mark=True)
    except Exception:  # pragma: no cover - depends on malformed entities
        return ""
    return "" if peer_id is None else str(peer_id)


def slugify(value: str | None) -> str:
    """Lowercase, punctuation-free form of a title or username (may be empty)."""
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


def display_name(title: str, username: str | None, channel: str) -> str:
    """Pick a readable name for ``channels.json``.

    Non-Latin titles slug to nothing, so fall back to the username and finally
    to the numeric id instead of a generic placeholder.
    """
    return (
        slugify(title)
        or slugify(username)
        or f"channel_{channel.lstrip('-')}"
    )


def kind_of(entity: Any) -> str:
    """Classify a dialog entity as ``channel``, ``group`` or ``chat``."""
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False):
        return "group"
    return "chat"


def tokens_for(value: str | None) -> set[str]:
    """Normalise an id, ``@username`` or display name for comparison."""
    if not value:
        return set()
    text = value.strip().lower()
    bare = text.lstrip("@")
    return {text, bare, f"@{bare}"}


def suggest_timezone(texts: Iterable[str]) -> str | None:
    """Map the most common GMT/UTC mention to a pytz ``Etc/GMT`` name.

    ``Etc/GMT`` offsets are inverted: a post saying "GMT+2" (UTC+2) becomes
    ``Etc/GMT-2``. Treat the result as a hint to verify, not as truth.
    """
    counts: dict[tuple[str, int], int] = {}
    for text in texts:
        for sign, hours in TIMEZONE_PATTERN.findall(text or ""):
            key = (sign, int(hours))
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    sign, hours = max(counts, key=lambda key: counts[key])
    return f"Etc/GMT{'-' if sign == '+' else '+'}{hours}"


async def collect(
    client: TelegramClient,
    *,
    only: str,
    search: str | None,
    timezone_hints: bool,
    hint_messages: int,
) -> list[dict[str, Any]]:
    """Walk the account's dialogs and describe each channel or group."""
    configs = load_channels()
    configured_tokens: set[str] = set()
    for config in configs:
        configured_tokens |= tokens_for(config.channel)
        configured_tokens |= tokens_for(config.name)

    needle = search.lower() if search else None
    rows: list[dict[str, Any]] = []

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if entity is None or getattr(dialog, "is_user", False):
            continue

        kind = kind_of(entity)
        if only == "channel" and kind != "channel":
            continue
        if only == "group" and kind == "channel":
            continue

        channel = marked_id(entity)
        if not channel:
            continue

        title = getattr(entity, "title", None) or dialog.name or ""
        username = getattr(entity, "username", None)
        slug = slugify(title)
        name = display_name(title, username, channel)

        if needle:
            haystack = [title.lower(), channel, slug, name]
            if username:
                haystack.append(username.lower())
            if not any(needle in value for value in haystack):
                continue

        last_post = getattr(getattr(dialog, "message", None), "date", None)

        row: dict[str, Any] = {
            "name": name,
            "title": title,
            "channel": channel,
            "username": f"@{username}" if username else None,
            "type": kind,
            "participants": getattr(entity, "participants_count", None),
            "last_message_id": getattr(getattr(dialog, "message", None), "id", None),
            "last_post_at": last_post.isoformat() if last_post else None,
            "configured": bool(
                configured_tokens
                & (tokens_for(channel) | tokens_for(username) | tokens_for(slug))
            ),
            "timezone_hint": None,
        }

        if timezone_hints:
            try:
                texts = [
                    message.message
                    async for message in client.iter_messages(entity, limit=hint_messages)
                ]
                row["timezone_hint"] = suggest_timezone(texts)
            except Exception as exc:  # restricted channel or flood wait
                row["timezone_hint"] = None
                print(f"  (could not scan {title!r}: {exc})", file=sys.stderr)

        rows.append(row)

    # Migrated groups surface twice: the old basic chat and the supergroup. The
    # supergroup id is the one that keeps working, so drop the legacy duplicate.
    upgraded = {row["title"].lower() for row in rows if row["type"] != "chat"}
    rows = [
        row for row in rows if row["type"] != "chat" or row["title"].lower() not in upgraded
    ]

    rows.sort(key=lambda row: (not row["configured"], row["title"].lower()))
    return rows


def format_table(rows: list[dict[str, Any]]) -> str:
    """Render the channel list as a fixed-width table."""
    headers = ["", "NAME", "ID", "USERNAME", "TYPE", "SUBS", "LAST POST", "TZ HINT"]
    table = [
        [
            "X" if row["configured"] else ".",
            row["name"],
            row["channel"],
            row["username"] or "-",
            row["type"],
            f"{row['participants']:,}" if row["participants"] else "-",
            (row["last_post_at"] or "-")[:10],
            row["timezone_hint"] or "-",
        ]
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(line[index]) for line in table))
        for index in range(len(headers))
    ]
    lines = [
        "  ".join(headers[index].ljust(widths[index]) for index in range(len(headers))).rstrip(),
        "  ".join("-" * widths[index] for index in range(len(headers))),
    ]
    lines.extend(
        "  ".join(line[index].ljust(widths[index]) for index in range(len(headers))).rstrip()
        for line in table
    )
    return "\n".join(lines)


def append_channels(path: str, rows: list[dict[str, Any]], *, enable: bool) -> list[dict[str, Any]]:
    """Append channels missing from ``path`` and return the new entries."""
    file = Path(path)
    document: dict[str, Any] = {}
    if file.is_file():
        loaded = json.loads(file.read_text(encoding="utf-8"))
        document = loaded if isinstance(loaded, dict) else {"channels": loaded}
    else:
        document = {
            "$comment": (
                "Telegram source channels. Safe to commit and share between hosts "
                "(no secrets here). Override the path with TELEGRAM_CHANNELS_FILE in .env."
            )
        }

    entries = document.setdefault("channels", [])
    if not isinstance(entries, list):
        raise RuntimeError(f"{path} does not contain a channel list")

    known_ids: set[str] = set()
    known_names: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            if entry.get("channel"):
                known_ids.add(str(entry["channel"]))
            if entry.get("name"):
                known_names.add(str(entry["name"]))
        elif isinstance(entry, str):
            known_ids.add(entry)

    added: list[dict[str, Any]] = []
    for row in rows:
        if not row["channel"] or row["configured"] or row["channel"] in known_ids:
            continue
        name, index = row["name"], 2
        while name in known_names:
            name, index = f"{row['name']}_{index}", index + 1
        known_names.add(name)
        known_ids.add(row["channel"])
        entry = {
            "name": name,
            "channel": row["channel"],
            "timezone": None,
            "provider": None,
            "enabled": bool(enable),
        }
        entries.append(entry)
        added.append(entry)

    if added:
        file.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return added


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List the Telegram channels this session can read.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-s", "--search", help="only show entries whose title, @username, name or id matches"
    )
    parser.add_argument(
        "--only",
        choices=("all", "channel", "group"),
        default="all",
        help="restrict the listing to broadcast channels or to groups (default: all)",
    )
    parser.add_argument(
        "--timezone-hints",
        action="store_true",
        help="scan recent posts for GMT/UTC mentions and suggest an Etc/GMT value",
    )
    parser.add_argument(
        "--hint-messages",
        type=int,
        default=50,
        help="posts to scan per channel with --timezone-hints (default: 50)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="append channels that are not in the channel file yet",
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        help="with --write, save new entries as enabled (default: disabled for review)",
    )
    parser.add_argument(
        "--channels-file",
        help=f"channel file to update (default: TELEGRAM_CHANNELS_FILE or {DEFAULT_CHANNELS_FILE})",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON on stdout")
    return parser


async def run(args: argparse.Namespace) -> int:
    client = await ensure_login()
    try:
        rows = await collect(
            client,
            only=args.only,
            search=args.search,
            timezone_hints=args.timezone_hints,
            hint_messages=args.hint_messages,
        )
    finally:
        await client.disconnect()

    status = sys.stderr if args.json else sys.stdout

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    elif rows:
        print(format_table(rows))
        configured = sum(1 for row in rows if row["configured"])
        print(
            f"\n{len(rows)} channel(s): {configured} already listed "
            f"(X), {len(rows) - configured} new (.)."
        )
        if args.timezone_hints and any(row["timezone_hint"] for row in rows):
            print("TZ HINT is guessed from recent posts - verify before relying on it.")
    else:
        print("No channels matched. Log in with the account that subscribes to them.")

    if args.write:
        path = args.channels_file or os.getenv("TELEGRAM_CHANNELS_FILE", DEFAULT_CHANNELS_FILE)
        try:
            added = append_channels(path, rows, enable=args.enable)
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            print(f"Could not update {path}: {exc}", file=sys.stderr)
            return 1
        if added:
            state = "enabled" if args.enable else "disabled (review, set a timezone, then enable)"
            print(f"\nAdded {len(added)} entry(ies) to {path} - {state}:", file=status)
            for entry in added:
                print(f"  {entry['name']}: {entry['channel']}", file=status)
        else:
            print(f"\n{path} already lists every channel found.", file=status)

    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.hint_messages < 1:
        print("--hint-messages must be at least 1", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
