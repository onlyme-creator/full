#!/usr/bin/env python3
"""
merge.py — IPTV Playlist & EPG Builder
Reads 'my_channels' (your custom #EXTINF lines), fetches third-party M3U
sources to find matching stream URLs, then writes playlist.m3u and epg.xml.

Sources span two repositories:
  • BuddyChewChew/full         — original sources
  • onlyme-creator/myt1        — second personal repo (extra channels)

Passthrough sources are written directly into the playlist as-is (live events).

Alternates ([ALT_CHANNELS] in my_channels): a second stream for a channel you
already have, taken from ALT_SOURCE (doms9). Their #EXTVLCOPT header lines
(user-agent / referrer) are kept, and their token URLs are fetched fresh on
every run, which is why the playlist-only mode below is meant to run often.

Usage:
  python merge.py                  full build  (playlist.m3u + epg.xml)
  python merge.py --playlist-only  quick build (playlist.m3u only, epg.xml untouched)
"""

import os
import re
import sys
import gzip
import io
import requests

# ─────────────────────────────────────────────────────────────────────────────
# 1.  SOURCE LISTS  ← paste / edit your URLs here
# ─────────────────────────────────────────────────────────────────────────────

# Standard M3U / M3U8 sources (fixed/permanent channels)
M3U_SOURCES = [
    # ── BuddyChewChew/full (original) ────────────────────────────────────────
    "https://raw.githubusercontent.com/BuddyChewChew/tcl-playlist-generator/refs/heads/main/tcl.m3u8",
    "https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/refs/heads/main/playlists/plex_us.m3u",
    "https://raw.githubusercontent.com/BuddyChewChew/My-Streams/refs/heads/main/tv.m3u",
    "https://raw.githubusercontent.com/BuddyChewChew/samsungtvplus/refs/heads/main/output/samsung_tvplus.m3u",
    "https://raw.githubusercontent.com/BuddyChewChew/pluto/refs/heads/main/pluto_us.m3u",

    # ── onlyme-creator/myt1 (second personal repo) ───────────────────────────
    "https://raw.githubusercontent.com/onlyme-creator/myt1/refs/heads/main/playlist.m3u",

    # ← Add more M3U/M3U8 source URLs here, one per line (keep trailing comma)
]

# Live-events / sports sources — used as LOOKUP only (not passthrough)
M3U8_LIVE_SOURCES = [
    # ← Add lookup-only live sources here if needed
]

# Passthrough sources — written DIRECTLY into playlist.m3u as-is every run.
# Do NOT add these channels to my_channels. The content updates automatically.
PASSTHROUGH_SOURCES = [
    # ← Add passthrough live-event sources here when ready
]

# Alternate-stream source. Entries listed under [ALT_CHANNELS] in my_channels
# are pulled from here WITH their #EXTVLCOPT header lines and labelled "(Alt)".
ALT_SOURCE = "https://raw.githubusercontent.com/doms9/iptv/refs/heads/default/M3U8/TV.m3u8"
ALT_LABEL  = "(Alt)"

# Live events from doms9, filtered to the sports below. Headers (#EXTVLCOPT) are
# kept and the list is rebuilt on every run (event links expire fast).
LIVE_EVENTS_SOURCE = "https://raw.githubusercontent.com/doms9/iptv/refs/heads/default/M3U8/events.m3u8"

# An event is kept if its tvg-id is in LIVE_EVENT_IDS, OR the [tag] at the start
# of its name contains one of LIVE_EVENT_KEYWORDS (case-insensitive).
# Remove a word/id to drop that sport; add one to include more.
LIVE_EVENT_IDS = {
    "NFL.Dummy.us",              # American football
    "Football.Dummy.us",         # NFL Network / RedZone entries
    "NBA.Basketball.Dummy.us",   # basketball
    "MLB.Baseball.Dummy.us",     # baseball
    "Baseball.Dummy.us",
}
LIVE_EVENT_KEYWORDS = [
    "american football", "nfl", "ncaaf", "college football",
    "basketball", "nba", "wnba", "ncaab",
    "baseball", "mlb",
    "tennis", "atp", "wta", "us open", "wimbledon", "roland garros",
    "australian open", "davis cup", "grand slam",
    "boxing", "boxeo", "pbc", "top rank", "golden boy", "matchroom",
]

# EPG / XML sources  (plain .xml  OR  gzip-compressed .xml.gz are both fine)
XML_SOURCES = [
    # ── BuddyChewChew/full (original) ────────────────────────────────────────
    "https://raw.githubusercontent.com/doms9/iptv/refs/heads/default/M3U8/TV.xml",
    "https://github.com/matthuisman/i.mjh.nz/raw/master/Plex/us.xml.gz",
    "https://raw.githubusercontent.com/BuddyChewChew/tcl-playlist-generator/refs/heads/main/tcl_epg.xml",
    "https://raw.githubusercontent.com/BuddyChewChew/samsungtvplus/refs/heads/main/output/samsung_tvplus.xml",
    "https://github.com/matthuisman/i.mjh.nz/raw/master/PlutoTV/us.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz",

    # ── onlyme-creator/myt1 (second personal repo) ───────────────────────────
    "https://raw.githubusercontent.com/onlyme-creator/myt1/refs/heads/main/shrunk_epg.xml",

    # ← Add more XML/XML.GZ EPG source URLs here
]

# ─────────────────────────────────────────────────────────────────────────────
# 2.  SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

# The script uses whichever of these exists in the repo
MY_CHANNEL_FILE_CANDIDATES = ["my_channels", "my_channels.txt"]
OUTPUT_PLAYLIST  = "playlist.m3u"
OUTPUT_EPG       = "epg.xml"
REQUEST_TIMEOUT  = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ─────────────────────────────────────────────────────────────────────────────
# 3.  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_text(url: str) -> str:
    """Download a URL and return its text content (handles .gz transparently)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        if url.endswith(".gz"):
            with gzip.open(io.BytesIO(r.content)) as f:
                return f.read().decode("utf-8", errors="replace")
        return r.text
    except Exception as exc:
        print(f"  [WARN] Could not fetch {url}: {exc}")
        return ""


def normalise(name: str) -> str:
    """Lower-case and strip all non-alphanumeric chars for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def extract_attr(line: str, attr: str) -> str:
    """Pull a quoted attribute value from an #EXTINF line."""
    m = re.search(rf'{attr}="([^"]*)"', line)
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
# 4.  LOAD CHANNELS from my_channels
# ─────────────────────────────────────────────────────────────────────────────

def find_channels_file() -> str:
    for cand in MY_CHANNEL_FILE_CANDIDATES:
        if os.path.exists(cand):
            return cand
    sys.exit(f"[ERROR] None of {MY_CHANNEL_FILE_CANDIDATES} found in {os.getcwd()}")


def load_alt_ids(path: str) -> list:
    """Read the [ALT_CHANNELS] section: one doms9 tvg-id per line ('#' = comment)."""
    ids, in_alt = [], False
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("[") and "]" in line:
                in_alt = line.upper().startswith("[ALT_CHANNELS]")
                continue
            if in_alt and line and not line.startswith("#"):
                ident = line.split("|")[0].strip()
                if ident and ident not in ids:
                    ids.append(ident)
    print(f"[INFO] {len(ids)} alternate channel ids requested")
    return ids


def load_my_channels(path: str) -> list:
    channels  = []
    seen_keys = set()

    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                continue
            if line.startswith("http"):
                continue
            if not line.startswith("#EXTINF"):
                continue

            tvg_id   = extract_attr(line, "tvg-id")
            tvg_name = extract_attr(line, "tvg-name")

            channel_name = line.rsplit(",", 1)[-1].strip() if "," in line else tvg_name
            match_name   = tvg_name if tvg_name else channel_name

            key = (tvg_id, normalise(match_name))
            if key in seen_keys:
                continue
            seen_keys.add(key)

            channels.append({
                "extinf":     line,
                "name":       channel_name,
                "match_name": match_name,
                "norm_name":  normalise(match_name),
                "tvg_id":     tvg_id,
            })

    print(f"[INFO] Loaded {len(channels)} unique channels from '{path}'")
    return channels


# ─────────────────────────────────────────────────────────────────────────────
# 5.  BUILD STREAM LOOKUP from all M3U sources
# ─────────────────────────────────────────────────────────────────────────────

def build_stream_lookup(sources: list) -> tuple[dict, dict]:
    name_to_url: dict[str, str] = {}
    id_to_url:   dict[str, str] = {}

    for url in sources:
        print(f"[INFO] Fetching M3U: {url}")
        text = fetch_text(url)
        if not text:
            continue

        lines = text.splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i].strip()
            if raw.startswith("#EXTINF"):
                stream_url = ""
                j = i + 1
                while j < len(lines):
                    candidate = lines[j].strip()
                    if candidate and not candidate.startswith("#"):
                        stream_url = candidate
                        break
                    j += 1

                if stream_url:
                    src_id      = extract_attr(raw, "tvg-id")
                    src_name    = extract_attr(raw, "tvg-name")
                    src_display = raw.rsplit(",", 1)[-1].strip() if "," in raw else ""
                    norm_src    = normalise(src_name or src_display)

                    if norm_src and norm_src not in name_to_url:
                        name_to_url[norm_src] = stream_url
                    if src_id and src_id not in id_to_url:
                        id_to_url[src_id] = stream_url

                i = j + 1
            else:
                i += 1

    print(
        f"[INFO] Stream lookup built — "
        f"{len(id_to_url)} by tvg-id, {len(name_to_url)} by name"
    )
    return name_to_url, id_to_url


# ─────────────────────────────────────────────────────────────────────────────
# 5b. ALTERNATES — header-preserving lookup from ALT_SOURCE, keyed by tvg-id
# ─────────────────────────────────────────────────────────────────────────────

def build_alt_lookup(url: str) -> dict:
    """tvg-id -> {"extinf": str, "opts": [#EXTVLCOPT/#EXTHTTP lines], "url": str}"""
    print(f"[INFO] Fetching alternates: {url}")
    text = fetch_text(url)
    alts: dict = {}
    if not text:
        return alts

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if not raw.startswith("#EXTINF"):
            i += 1
            continue
        opts, stream_url, j = [], "", i + 1
        while j < len(lines):
            cand = lines[j].strip()
            if cand.startswith("#EXTINF"):
                break
            if cand.startswith("#EXTVLCOPT") or cand.startswith("#EXTHTTP"):
                opts.append(cand)
            elif cand and not cand.startswith("#"):
                stream_url = cand
                break
            j += 1
        tid = extract_attr(raw, "tvg-id")
        if stream_url and tid and tid not in alts:
            alts[tid] = {"extinf": raw, "opts": opts, "url": stream_url}
        i = j + 1 if stream_url else j

    print(f"[INFO] Alternates lookup built — {len(alts)} channels")
    return alts


def alt_extinf(extinf: str) -> str:
    """Label the display name '(Alt)' and drop tvg-chno so numbering never clashes."""
    extinf = re.sub(r'\s*tvg-chno="[^"]*"', "", extinf)
    head, _, name = extinf.rpartition(",")
    return f"{head},{name.strip()} {ALT_LABEL}"


# ─────────────────────────────────────────────────────────────────────────────
# 5c. LIVE EVENTS — filtered by sport, headers kept
# ─────────────────────────────────────────────────────────────────────────────

def build_live_events(url: str) -> list:
    """Return filtered event blocks: each is a list of lines (EXTINF, opts, URL)."""
    print(f"[INFO] Fetching live events: {url}")
    text = fetch_text(url)
    blocks, total = [], 0
    if not text:
        return blocks

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if not raw.startswith("#EXTINF"):
            i += 1
            continue
        block, stream_url, j = [raw], "", i + 1
        while j < len(lines):
            cand = lines[j].strip()
            if cand.startswith("#EXTINF"):
                break
            if cand.startswith("#"):
                block.append(cand)
            elif cand:
                block.append(cand)
                stream_url = cand
                break
            j += 1
        i = j + 1 if stream_url else j
        if not stream_url:
            continue
        total += 1

        tid  = extract_attr(raw, "tvg-id")
        name = extract_attr(raw, "tvg-name") or raw.rsplit(",", 1)[-1]
        m    = re.match(r"\s*\[([^\]]*)\]", name)
        tag  = (m.group(1) if m else "").lower()
        if tid in LIVE_EVENT_IDS or any(k in tag for k in LIVE_EVENT_KEYWORDS):
            blocks.append(block)

    print(f"[INFO] Live events: kept {len(blocks)} of {total}")
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# 6.  WRITE playlist.m3u
#     Part A — matched permanent channels from my_channels
#     Part B — passthrough live events appended directly
# ─────────────────────────────────────────────────────────────────────────────

def write_playlist(
    channels: list,
    name_lookup: dict,
    id_lookup: dict,
    passthrough_sources: list,
    out_path: str,
    alt_ids: list = (),
    alt_lookup: dict = None,
    live_events: list = (),
) -> set:
    """Returns the set of tvg-ids present in the written playlist."""
    matched   = 0
    unmatched = []
    written_ids: set = set()

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")

        # ── Part A: permanent channels ────────────────────────────────────────
        for ch in channels:
            stream = id_lookup.get(ch["tvg_id"], "")
            if not stream:
                stream = name_lookup.get(ch["norm_name"], "")

            if stream:
                fh.write(ch["extinf"] + "\n")
                fh.write(stream + "\n")
                matched += 1
                if ch["tvg_id"]:
                    written_ids.add(ch["tvg_id"])
            else:
                unmatched.append(ch["name"])

        # ── Part A2: alternates (with their header lines) ─────────────────────
        alt_written, alt_missing = 0, []
        for tid in alt_ids:
            a = (alt_lookup or {}).get(tid)
            if not a:
                alt_missing.append(tid)
                continue
            fh.write(alt_extinf(a["extinf"]) + "\n")
            for opt in a["opts"]:
                fh.write(opt + "\n")
            fh.write(a["url"] + "\n")
            alt_written += 1
            written_ids.add(tid)

        # ── Part A3: filtered live events from LIVE_EVENTS_SOURCE ─────────────
        for block in live_events:
            for line in block:
                fh.write(line + "\n")

        # ── Part B: passthrough live events ───────────────────────────────────
        total_passthrough = len(live_events)
        for url in passthrough_sources:
            print(f"[INFO] Passthrough: {url}")
            text = fetch_text(url)
            if not text:
                continue

            count = 0
            lines = text.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i].strip()

                # Skip the #EXTM3U header line — we already wrote our own
                if line.startswith("#EXTM3U"):
                    i += 1
                    continue

                # Write every line as-is (EXTINF, EXTVLCOPT, stream URL, blank)
                fh.write(line + "\n")

                if line.startswith("#EXTINF"):
                    count += 1

                i += 1

            print(f"  → {count} live event entries written from passthrough")
            total_passthrough += count

    print(
        f"[INFO] playlist.m3u → {matched} permanent + {alt_written} alternates "
        f"+ {total_passthrough} live event entries"
    )
    if alt_missing:
        print(f"[WARN] {len(alt_missing)} alternates not found in ALT_SOURCE: {', '.join(alt_missing)}")
    if unmatched:
        print(f"[WARN] {len(unmatched)} permanent channels had no stream match:")
        for n in unmatched:
            print(f"         • {n}")
    return written_ids


# ─────────────────────────────────────────────────────────────────────────────
# 7.  WRITE epg.xml
# ─────────────────────────────────────────────────────────────────────────────

def write_epg(channels: list, xml_sources: list, out_path: str, extra_ids=()):
    wanted_ids = {ch["tvg_id"] for ch in channels if ch["tvg_id"]} | set(extra_ids)
    print(f"[INFO] EPG: looking for {len(wanted_ids)} unique tvg-ids")

    collected_channels:   list[str] = []
    collected_programmes: list[str] = []
    seen_channel_ids:     set[str]  = set()
    seen_programmes:      set[tuple] = set()

    channel_re = re.compile(
        r'<channel\s[^>]*id="([^"]*)"[^>]*/?\s*>(?:.*?</channel>)?',
        re.DOTALL
    )
    programme_re = re.compile(
        r'<programme\b[^>]*channel="([^"]*)"[^>]*/?>(?:.*?</programme>)?',
        re.DOTALL
    )

    for url in xml_sources:
        print(f"[INFO] Fetching XML: {url}")
        text = fetch_text(url)
        if not text:
            continue

        for m in channel_re.finditer(text):
            cid  = m.group(1)
            blob = m.group(0)
            if cid in wanted_ids and cid not in seen_channel_ids:
                collected_channels.append(blob)
                seen_channel_ids.add(cid)

        for m in programme_re.finditer(text):
            if m.group(1) in wanted_ids:
                st = re.search(r'start="([^"]*)"', m.group(0))
                key = (m.group(1), st.group(1) if st else m.group(0))
                if key in seen_programmes:
                    continue          # same show already supplied by an earlier source
                seen_programmes.add(key)
                collected_programmes.append(m.group(0))

    print(
        f"[INFO] EPG: collected {len(collected_channels)} channel entries, "
        f"{len(collected_programmes)} programme entries"
    )

    no_guide = sorted(wanted_ids - {m.group(1) for b in collected_channels
                                    for m in [re.search(r'id="([^"]*)"', b)] if m})
    if no_guide:
        print(f"[WARN] {len(no_guide)} playlist tvg-ids have no guide data in any EPG source:")
        for cid in no_guide:
            print(f"         • {cid}")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        fh.write('<tv generator-info-name="merge.py">\n\n')
        for block in collected_channels:
            fh.write(block.strip() + "\n")
        fh.write("\n")
        for block in collected_programmes:
            fh.write(block.strip() + "\n")
        fh.write("\n</tv>\n")

    print(f"[INFO] epg.xml written → {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# 8.  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    playlist_only = "--playlist-only" in sys.argv
    all_m3u_sources = M3U_SOURCES + M3U8_LIVE_SOURCES
    channels_file = find_channels_file()

    print("=" * 60)
    print(f"Step 1 – Loading {channels_file}" + ("   [playlist-only mode]" if playlist_only else ""))
    print("=" * 60)
    channels = load_my_channels(channels_file)
    alt_ids  = load_alt_ids(channels_file)

    print("\n" + "=" * 60)
    print("Step 2 – Building stream lookup from M3U sources")
    print("=" * 60)
    name_lookup, id_lookup = build_stream_lookup(all_m3u_sources)
    alt_lookup = build_alt_lookup(ALT_SOURCE) if alt_ids else {}
    live_events = build_live_events(LIVE_EVENTS_SOURCE) if LIVE_EVENTS_SOURCE else []

    # Safety net: if the alternates source is down, fail the run instead of
    # publishing a playlist with the alternates silently missing.
    if alt_ids and not alt_lookup:
        sys.exit("[ERROR] Alternates source returned nothing; leaving existing files untouched.")

    print("\n" + "=" * 60)
    print("Step 3 – Writing playlist.m3u (permanent + alternates + live events)")
    print("=" * 60)
    written_ids = write_playlist(
        channels, name_lookup, id_lookup, PASSTHROUGH_SOURCES, OUTPUT_PLAYLIST,
        alt_ids, alt_lookup, live_events,
    )

    if playlist_only:
        print("\n[INFO] Playlist-only mode: epg.xml left unchanged.")
    else:
        print("\n" + "=" * 60)
        print("Step 4 – Writing epg.xml")
        print("=" * 60)
        write_epg(channels, XML_SOURCES, OUTPUT_EPG, extra_ids=[t for t in alt_ids if t in written_ids])

    print("\n" + "=" * 60)
    print("Done!  Files written:")
    print(f"  • {OUTPUT_PLAYLIST}")
    if not playlist_only:
        print(f"  • {OUTPUT_EPG}")
    print("=" * 60)


if __name__ == "__main__":
    main()
