
#!/usr/bin/env python3
"""MEGA Xtream sports scanner: USA, UK and Canada master list."""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests

BASE_URL = os.environ.get("MEGA_XTREAM_URL", "").strip().rstrip("/")
USERNAME = os.environ.get("MEGA_XTREAM_USERNAME", "").strip()
PASSWORD = os.environ.get("MEGA_XTREAM_PASSWORD", "")

OUT = Path("mega-sports-master")
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 45
MAX_ATTEMPTS = 3

if not BASE_URL or not USERNAME or not PASSWORD:
    sys.exit(
        "Missing one or more MEGA_XTREAM_* secrets. "
        "Credential values are not printed."
    )

# Add a scheme if the saved URL does not contain one.
if not re.match(r"^https?://", BASE_URL, flags=re.I):
    BASE_URL = "http://" + BASE_URL

# Build the Xtream API endpoint without duplicating player_api.php.
parsed = urlparse(BASE_URL)

if not parsed.hostname:
    sys.exit(
        "MEGA_XTREAM_URL is invalid. Check the saved server URL."
    )

if parsed.scheme.lower() not in ("http", "https"):
    sys.exit("MEGA_XTREAM_URL must use HTTP or HTTPS.")

path = parsed.path.rstrip("/")

if path.lower().endswith("/player_api.php") or path.lower() == "player_api.php":
    api_path = path
else:
    api_path = path + "/player_api.php"

API_URL = urlunparse(
    (parsed.scheme, parsed.netloc, api_path, "", "", "")
)

SPORT_WORDS = re.compile(
    r"\b(sports?|mlb|nba|nfl|nhl|hockey|baseball|basketball|football|soccer|"
    r"rugby|cricket|tennis|golf|motorsports?|racing|boxing|ufc|mma|wwe|"
    r"espn|fox sports|sky sports|tsn|sportsnet|dazn|be[in]? sports|ppv|"
    r"league pass|redzone|fight|olympics?)\b",
    re.I,
)

COUNTRY_PREFIX = re.compile(
    r"(?:^|\b)(?:US|USA|UK|CA|CANADA|UNITED STATES|UNITED KINGDOM)"
    r"(?:\s*[:/|\-]|\b)",
    re.I,
)

COUNTRY_WORDS = re.compile(
    r"\b(?:US|USA|UK|CA|CANADA|UNITED STATES|UNITED KINGDOM)\b",
    re.I,
)

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (compatible; MEGA-Sports-Master-Scanner/1.1)"
    ),
    "Accept": "application/json, text/plain, */*",
})


def api_get(params=None):
    """Request the Xtream API with bounded retries and safe diagnostics."""

    query = {
        "username": USERNAME,
        "password": PASSWORD,
    }

    if params:
        query.update(params)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(
                API_URL,
                params=query,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )

            if response.status_code in (429, 500, 502, 503, 504):
                if attempt < MAX_ATTEMPTS:
                    delay = 2 ** (attempt - 1)
                    print(
                        f"MEGA API returned HTTP {response.status_code}. "
                        f"Retrying in {delay}s "
                        f"(attempt {attempt}/{MAX_ATTEMPTS})."
                    )
                    time.sleep(delay)
                    continue

                raise RuntimeError(
                    f"MEGA API returned HTTP {response.status_code} "
                    f"after {MAX_ATTEMPTS} attempts."
                )

            if response.status_code in (401, 403):
                raise RuntimeError(
                    f"MEGA API returned HTTP {response.status_code}. "
                    "Access was denied. Verify the endpoint and provider access."
                )

            response.raise_for_status()

            try:
                data = response.json()
            except ValueError:
                raise RuntimeError(
                    "MEGA API returned a non-JSON response "
                    f"(HTTP {response.status_code}). "
                    "Check the API endpoint and provider response."
                ) from None

            if isinstance(data, dict):
                info = data.get("user_info", {})

                if isinstance(info, dict):
                    auth = str(info.get("auth", "1")).lower()

                    if auth in ("0", "false"):
                        raise RuntimeError(
                            "MEGA API responded but rejected authentication. "
                            "Verify the saved credentials."
                        )

                message = str(data.get("message", "")).lower()

                if message in (
                    "invalid credentials",
                    "authentication failed",
                    "invalid username or password",
                ):
                    raise RuntimeError(
                        "MEGA API responded but rejected authentication. "
                        "Verify the saved credentials."
                    )

            return data

        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:

            if attempt < MAX_ATTEMPTS:
                delay = 2 ** (attempt - 1)

                print(
                    f"MEGA connection failed ({type(exc).__name__}). "
                    f"Retrying in {delay}s "
                    f"(attempt {attempt}/{MAX_ATTEMPTS})."
                )
                time.sleep(delay)
                continue

            raise RuntimeError(
                f"Could not connect to the MEGA API after "
                f"{MAX_ATTEMPTS} attempts ({type(exc).__name__}). "
                "The connection failure does not prove the credentials "
                "are incorrect. Check provider availability, endpoint "
                "configuration, and GitHub runner connectivity."
            ) from None


def main():
    print(
        "Connecting to MEGA Xtream API "
        "(credential values are hidden)..."
    )

    # Standard Xtream account-info request: no action parameter.
    account = api_get()

    if not isinstance(account, dict):
        sys.exit(
            "MEGA API responded, but the account response was not "
            "a JSON object. Check endpoint compatibility."
        )

    info = account.get("user_info", {})

    if isinstance(info, dict):
        auth = str(info.get("auth", "1")).lower()

        if auth in ("0", "false"):
            sys.exit(
                "MEGA authentication failed. Verify the repository secrets."
            )

    print("Account endpoint responded.")

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

    # Find sports categories associated with USA, UK or Canada.
    selected_categories = {}

    for cid, name in category_map.items():
        has_country = bool(
            COUNTRY_PREFIX.search(name) or COUNTRY_WORDS.search(name)
        )
        has_sports = bool(SPORT_WORDS.search(name))

        if has_country and has_sports:
            selected_categories[cid] = name

    # Also include country/sport matches in channel names when the
    # provider uses a generic category label.
    records = []

    for stream in streams:
        if not isinstance(stream, dict):
            continue

        sid = str(
            stream.get("stream_id", stream.get("id", ""))
        ).strip()

        name = str(
            stream.get("name", stream.get("stream_name", ""))
        ).strip()

        cid = str(stream.get("category_id", "")).strip()

        category_name = category_map.get(
            cid, "(unknown category)"
        )

        searchable = f"{category_name} {name}"

        has_country = bool(
            COUNTRY_PREFIX.search(searchable)
            or COUNTRY_WORDS.search(searchable)
        )

        has_sports = bool(SPORT_WORDS.search(searchable))

        if cid in selected_categories or (has_country and has_sports):
            records.append({
                "category_id": cid,
                "category_name": category_name,
                "stream_id": sid,
                "channel_name": name,
                "country_sports_group_match": (
                    cid in selected_categories
                ),
                "stream_icon": str(
                    stream.get("stream_icon", "") or ""
                ),
                "epg_channel_id": str(
                    stream.get("epg_channel_id", "") or ""
                ),
            })

    # Stable ordering while preserving the provider's IDs.
    records.sort(
        key=lambda r: (
            r["category_name"].casefold(),
            r["channel_name"].casefold(),
            r["stream_id"],
        )
    )

    OUT.mkdir(parents=True, exist_ok=True)
    run_at = datetime.now(timezone.utc).isoformat()

    # Plain-text master list.
    with (OUT / "mega_sports_master.txt").open(
        "w", encoding="utf-8", newline=""
    ) as f:
        f.write("MEGA SPORTS MASTER LIST — USA / UK / CANADA\n")
        f.write(f"Generated UTC: {run_at}\n")
        f.write(f"Provider categories scanned: {len(category_map)}\n")
        f.write(f"Live streams scanned: {len(streams)}\n")
        f.write(
            "Sports-related country groups selected: "
            f"{len(selected_categories)}\n"
        )
        f.write(f"Matching channel records: {len(records)}\n\n")
        f.write(
            "FORMAT: CATEGORY ID | CATEGORY NAME | STREAM ID | "
            "CHANNEL NAME | EPG CHANNEL ID\n"
        )

        for r in records:
            f.write(
                f"{r['category_id']} | {r['category_name']} | "
                f"{r['stream_id']} | {r['channel_name']} | "
                f"{r['epg_channel_id']}\n"
            )

    # CSV master list.
    with (OUT / "mega_sports_master.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "category_id",
                "category_name",
                "stream_id",
                "channel_name",
                "epg_channel_id",
                "stream_icon",
                "country_sports_group_match",
            ],
        )
        writer.writeheader()
        writer.writerows(records)

    # JSON master list.
    summary = {
        "provider": "MEGA",
        "generated_utc": run_at,
        "categories_scanned": len(category_map),
        "live_streams_scanned": len(streams),
        "selected_country_sports_categories": [
            {
                "category_id": cid,
                "category_name": name,
            }
            for cid, name in sorted(
                selected_categories.items(),
                key=lambda item: item[1].casefold(),
            )
        ],
        "matching_channel_count": len(records),
        "channels": records,
    }

    with (OUT / "mega_sports_master.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"Categories scanned: {len(category_map)}")
    print(f"Live streams scanned: {len(streams)}")
    print(
        "USA/UK/Canada sports groups matched: "
        f"{len(selected_categories)}"
    )
    print(f"Matching channels written: {len(records)}")
    print(f"Output directory: {OUT}")
    print(
        "Created mega_sports_master.txt, "
        "mega_sports_master.csv, and mega_sports_master.json."
    )

    if not records:
        print(
            "WARNING: No matches found. Inspect provider category "
            "names and matching rules."
        )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        sys.exit(f"ERROR: {exc}")
