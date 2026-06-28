#!/usr/bin/env python3
"""
merge.py — IPTV Playlist & EPG Builder
Reads 'my_channels' (your custom #EXTINF lines), fetches third-party M3U
sources to find matching stream URLs, then writes playlist.m3u and epg.xml.

Sources span two repositories:
  • BuddyChewChew/full         — original sources
  • onlyme-creator/myt1        — second personal repo (extra channels)

Passthrough sources are written directly into the playlist as-is (live events).
"""

import re
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

MY_CHANNELS_FILE = "my_channels"
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
):
    matched   = 0
    unmatched = []

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
            else:
                unmatched.append(ch["name"])

        # ── Part B: passthrough live events ───────────────────────────────────
        total_passthrough = 0
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

    print(f"[INFO] playlist.m3u → {matched} permanent + {total_passthrough} live event entries")
    if unmatched:
        print(f"[WARN] {len(unmatched)} permanent channels had no stream match:")
        for n in unmatched:
            print(f"         • {n}")


# ─────────────────────────────────────────────────────────────────────────────
# 7.  WRITE epg.xml
# ─────────────────────────────────────────────────────────────────────────────

def write_epg(channels: list, xml_sources: list, out_path: str):
    wanted_ids = {ch["tvg_id"] for ch in channels if ch["tvg_id"]}
    print(f"[INFO] EPG: looking for {len(wanted_ids)} unique tvg-ids")

    collected_channels:   list[str] = []
    collected_programmes: list[str] = []
    seen_channel_ids:     set[str]  = set()

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
                collected_programmes.append(m.group(0))

    print(
        f"[INFO] EPG: collected {len(collected_channels)} channel entries, "
        f"{len(collected_programmes)} programme entries"
    )

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
    all_m3u_sources = M3U_SOURCES + M3U8_LIVE_SOURCES

    print("=" * 60)
    print("Step 1 – Loading my_channels")
    print("=" * 60)
    channels = load_my_channels(MY_CHANNELS_FILE)

    print("\n" + "=" * 60)
    print("Step 2 – Building stream lookup from M3U sources")
    print("=" * 60)
    name_lookup, id_lookup = build_stream_lookup(all_m3u_sources)

    print("\n" + "=" * 60)
    print("Step 3 – Writing playlist.m3u (permanent + live events)")
    print("=" * 60)
    write_playlist(channels, name_lookup, id_lookup, PASSTHROUGH_SOURCES, OUTPUT_PLAYLIST)

    print("\n" + "=" * 60)
    print("Step 4 – Writing epg.xml")
    print("=" * 60)
    write_epg(channels, XML_SOURCES, OUTPUT_EPG)

    print("\n" + "=" * 60)
    print("Done!  Files written:")
    print(f"  • {OUTPUT_PLAYLIST}")
    print(f"  • {OUTPUT_EPG}")
    print("=" * 60)


if __name__ == "__main__":
    main()
