#!/usr/bin/env python3
"""Temporary MEGA Xtream scan: build a master list of USA/UK/Canada sports groups."""
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE_URL = os.environ.get("MEGA_XTREAM_URL", "").strip().rstrip("/")
USERNAME = os.environ.get("MEGA_XTREAM_USERNAME", "").strip()
PASSWORD = os.environ.get("MEGA_XTREAM_PASSWORD", "")
OUT = Path("mega-sports-master")
TIMEOUT = 45

if not BASE_URL or not USERNAME or not PASSWORD:
    sys.exit("Missing one or more MEGA_XTREAM_* secrets. Values are not printed.")

# Keep the provider's explicitly supplied scheme/port. Add http:// only when absent.
if not re.match(r"^https?://", BASE_URL, flags=re.I):
    BASE_URL = "http://" + BASE_URL

# If the secret includes a portal path, retain it and append player_api.php.
API_URL = BASE_URL + "/player_api.php"

SPORT_WORDS = re.compile(
    r"\b(sports?|mlb|nba|nfl|nhl|hockey|baseball|basketball|football|soccer|"
    r"rugby|cricket|tennis|golf|motorsports?|racing|boxing|ufc|mma|wwe|"
    r"espn|fox sports|sky sports|tsn|sportsnet|dazn|be[in]? sports|ppv|"
    r"league pass|redzone|fight|racing|olympics?)\b",
    re.I,
)
COUNTRY_PREFIX = re.compile(r"(?:^|\b)(?:US|USA|UK|CA|CANADA|UNITED STATES|UNITED KINGDOM)(?:\s*[:/|\-]|\b)", re.I)
COUNTRY_WORDS = re.compile(r"\b(?:US|USA|UK|CA|CANADA|UNITED STATES|UNITED KINGDOM|UNITED KINGDOM)\b", re.I)

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MEGA-Sports-Master-Scanner/1.0)"})

def api_get(params):
    query = {"username": USERNAME, "password": PASSWORD, **params}
    response = session.get(API_URL, params=query, timeout=TIMEOUT)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("Provider API returned a non-JSON response. Check the MEGA URL and credentials.")
    if isinstance(data, dict) and (data.get("user_info", {}).get("auth") == 0):
        raise RuntimeError("Provider rejected authentication. Verify the MEGA credentials.")
    return data

print("Connecting to MEGA provider API (credential values are hidden)...")
account = api_get({"action": "get_user_info"})
if isinstance(account, dict):
    info = account.get("user_info", {})
    if isinstance(info, dict) and str(info.get("auth", "1")) in ("0", "False", "false"):
        sys.exit("MEGA authentication failed. Verify the saved repository secrets.")
print("Authentication endpoint responded.")

categories = api_get({"action": "get_live_categories"})
streams = api_get({"action": "get_live_streams"})
if not isinstance(categories, list):
    sys.exit("Provider did not return a live-category list.")
if not isinstance(streams, list):
    sys.exit("Provider did not return a live-stream list.")

category_map = {}
for cat in categories:
    if not isinstance(cat, dict):
        continue
    cid = str(cat.get("category_id", "")).strip()
    name = str(cat.get("category_name", "")).strip()
    if cid:
        category_map[cid] = name or "(unnamed category)"

# Include only sports-oriented groups explicitly associated with USA/UK/Canada.
# Country-prefixed provider categories (e.g. US: NFL EVENTS, UK: SKY SPORTS) are supported.
selected_categories = {}
for cid, name in category_map.items():
    has_country = bool(COUNTRY_PREFIX.search(name) or COUNTRY_WORDS.search(name))
    has_sports = bool(SPORT_WORDS.search(name))
    if has_country and has_sports:
        selected_categories[cid] = name

# If the provider uses a generic category label, also retain channels whose own
# names clearly identify a country and sport; their original category is preserved.
records = []
for stream in streams:
    if not isinstance(stream, dict):
        continue
    sid = str(stream.get("stream_id", stream.get("id", ""))).strip()
    name = str(stream.get("name", stream.get("stream_name", ""))).strip()
    cid = str(stream.get("category_id", "")).strip()
    category_name = category_map.get(cid, "(unknown category)")
    searchable = f"{category_name} {name}"
    has_country = bool(COUNTRY_PREFIX.search(searchable) or COUNTRY_WORDS.search(searchable))
    has_sports = bool(SPORT_WORDS.search(searchable))
    if cid in selected_categories or (has_country and has_sports):
        records.append({
            "category_id": cid,
            "category_name": category_name,
            "stream_id": sid,
            "channel_name": name,
            "country_sports_group_match": cid in selected_categories,
            "stream_icon": str(stream.get("stream_icon", "") or ""),
            "epg_channel_id": str(stream.get("epg_channel_id", "") or ""),
        })

# Sort by provider group then exact channel name, while retaining provider IDs.
records.sort(key=lambda r: (r["category_name"].casefold(), r["channel_name"].casefold(), r["stream_id"]))
OUT.mkdir(parents=True, exist_ok=True)
run_at = datetime.now(timezone.utc).isoformat()

with (OUT / "mega_sports_master.txt").open("w", encoding="utf-8", newline="") as f:
    f.write("MEGA SPORTS MASTER LIST — USA / UK / CANADA\n")
    f.write(f"Generated UTC: {run_at}\n")
    f.write(f"Provider categories scanned: {len(category_map)}\n")
    f.write(f"Live streams scanned: {len(streams)}\n")
    f.write(f"Sports-related country groups selected: {len(selected_categories)}\n")
    f.write(f"Matching channel records: {len(records)}\n\n")
    f.write("FORMAT: CATEGORY ID | CATEGORY NAME | STREAM ID | CHANNEL NAME | EPG CHANNEL ID\n")
    for r in records:
        f.write(f"{r['category_id']} | {r['category_name']} | {r['stream_id']} | {r['channel_name']} | {r['epg_channel_id']}\n")

with (OUT / "mega_sports_master.csv").open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["category_id", "category_name", "stream_id", "channel_name", "epg_channel_id", "stream_icon", "country_sports_group_match"])
    writer.writeheader()
    writer.writerows(records)

summary = {
    "provider": "MEGA",
    "generated_utc": run_at,
    "categories_scanned": len(category_map),
    "live_streams_scanned": len(streams),
    "selected_country_sports_categories": [
        {"category_id": cid, "category_name": name}
        for cid, name in sorted(selected_categories.items(), key=lambda item: item[1].casefold())
    ],
    "matching_channel_count": len(records),
    "channels": records,
}
with (OUT / "mega_sports_master.json").open("w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print(f"Categories scanned: {len(category_map)}")
print(f"Live streams scanned: {len(streams)}")
print(f"USA/UK/Canada sports groups matched: {len(selected_categories)}")
print(f"Matching channels written: {len(records)}")
print("Created mega_sports_master.txt, .csv, and .json")
if not records:
    print("WARNING: no matches found. The artifact still includes empty master-list files; inspect provider category naming.")
