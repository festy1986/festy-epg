
#!/usr/bin/env python3
"""Temporary MEGA Xtream scanner for USA, UK and Canada sports channels."""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# GitHub Actions repository secrets
BASE_URL = os.environ.get("MEGA_XTREAM_URL", "").strip().rstrip("/")
USERNAME = os.environ.get("MEGA_XTREAM_USERNAME", "").strip()
PASSWORD = os.environ.get("MEGA_XTREAM_PASSWORD", "")

OUTPUT_DIR = Path("mega-sports-master")
TIMEOUT = (15, 90)
MAX_ATTEMPTS = 4

if not BASE_URL or not USERNAME or not PASSWORD:
    sys.exit(
        "ERROR: Missing MEGA_XTREAM_URL, MEGA_XTREAM_USERNAME, "
        "or MEGA_XTREAM_PASSWORD repository secrets. Values are hidden."
    )

if not re.match(r"^https?://", BASE_URL, re.I):
    BASE_URL = "http://" + BASE_URL

parsed = urlparse(BASE_URL)
if not parsed.hostname:
    sys.exit("ERROR: MEGA_XTREAM_URL is not a valid server URL.")

# The provider's working API endpoint is /player_api.php.
# Do not add credentials to the URL manually; requests encodes them safely.
API_URL = BASE_URL + "/player_api.php"

SPORT_WORDS = re.compile(
    r"\b("
    r"sports?|mlb|nba|nfl|nhl|hockey|baseball|basketball|football|soccer|"
    r"rugby|cricket|tennis|golf|motorsports?|racing|boxing|ufc|mma|wwe|"
    r"espn|fox\s*sports|sky\s*sports|tsn|sportsnet|dazn|be[in]?\s*sports|"
    r"ppv|league\s*pass|redzone|fight|olympics?|nascar|formula\s*1|f1|"
    r"college\s*(football|basketball)|ufc|wta|atp|mls|pga|nhl|wnba"
    r")\b",
    re.I,
)

COUNTRY_WORDS = re.compile(
    r"\b("
    r"US|USA|U\.S\.A\.?|United States|America|"
    r"UK|U\.K\.|United Kingdom|Britain|British|"
    r"CA|CAN|Canada|Canadian"
    r")\b",
    re.I,
)

COUNTRY_PREFIX = re.compile(
    r"^\s*(?:US|USA|UK|CA|CAN|CANADA|"
    r"UNITED STATES|UNITED KINGDOM)"
    r"\s*(?:[:/\-_|]|\b)",
    re.I,
)


def make_session():
    """Create a session with conservative retries for transient resets."""
    session = requests.Session()
    retry = Retry(
        total=MAX_ATTEMPTS - 1,
        connect=MAX_ATTEMPTS - 1,
        read=MAX_ATTEMPTS - 1,
        status=MAX_ATTEMPTS - 1,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                      "AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Connection": "close",
    })
    return session


SESSION = make_session()


def api_get(action=None):
    """Call the API using the same parameter pattern as the working login link."""
    params = {
        "username": USERNAME,
        "password": PASSWORD,
    }
    if action:
        params["action"] = action

    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = SESSION.get(
                API_URL,
                params=params,
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            try:
                data = response.json()
            except ValueError:
                raise RuntimeError(
                    "The MEGA API returned a non-JSON response."
                )

            if isinstance(data, dict):
                info = data.get("user_info", {})
                if isinstance(info, dict) and str(info.get("auth", "1")).lower() in (
                    "0", "false"
                ):
                    raise RuntimeError(
                        "MEGA rejected authentication. Check the saved secrets."
                    )

            return data

        except RuntimeError:
            raise
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                wait = min(2 ** attempt, 15)
                print(
                    f"Connection attempt {attempt}/{MAX_ATTEMPTS} failed "
                    f"({type(exc).__name__}). Retrying in {wait}s..."
                )
                time.sleep(wait)

    raise RuntimeError(
        "Could not reach the MEGA API after "
        f"{MAX_ATTEMPTS} attempts ({type(last_error).__name__}). "
        "The login URL may work from your browser while the GitHub runner "
        "is unable to establish a connection. Credentials are not printed."
    )


def as_list(data, description):
    if not isinstance(data, list):
        raise RuntimeError(
            f"MEGA did not return a valid {description} list."
        )
    return data


def main():
    print("Connecting to MEGA Xtream API.")
    print(f"API host: {parsed.hostname}")
    print("Credentials are hidden from logs.")

    # Match the known-working account-info request: no action parameter.
    account = api_get()

    if not isinstance(account, dict):
        sys.exit("ERROR: Unexpected response from the MEGA account endpoint.")

    user_info = account.get("user_info", {})
    if not isinstance(user_info, dict) or str(
        user_info.get("auth", "0")
    ) != "1":
        sys.exit("ERROR: MEGA authentication was not confirmed.")

    print("MEGA authentication confirmed.")

    # Fetch categories and streams independently so logs identify the failure.
    try:
        categories = as_list(
            api_get("get_live_categories"), "live-category"
        )
        print(f"Live categories received: {len(categories)}")
    except Exception as exc:
        sys.exit(f"ERROR fetching live categories: {exc}")

    try:
        streams = as_list(
            api_get("get_live_streams"), "live-stream"
        )
        print(f"Live streams received: {len(streams)}")
    except Exception as exc:
        sys.exit(f"ERROR fetching live streams: {exc}")

    category_map = {}
    for category in categories:
        if not isinstance(category, dict):
            continue

        category_id = str(category.get("category_id", "")).strip()
        category_name = str(category.get("category_name", "")).strip()

        if category_id:
            category_map[category_id] = category_name or "(unnamed category)"

    # Find categories explicitly identified as both a supported country
    # and a sports group. Names may change, so matching is name-based.
    selected_categories = {}
    for category_id, category_name in category_map.items():
        if COUNTRY_WORDS.search(category_name) and SPORT_WORDS.search(
            category_name
        ):
            selected_categories[category_id] = category_name

    records = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue

        stream_id = str(
            stream.get("stream_id", stream.get("id", ""))
        ).strip()
        channel_name = str(
            stream.get("name", stream.get("stream_name", ""))
        ).strip()
        category_id = str(stream.get("category_id", "")).strip()
        category_name = category_map.get(category_id, "(unknown category)")

        if not channel_name:
            continue

        category_match = category_id in selected_categories
        searchable = f"{category_name} {channel_name}"
        country_match = bool(COUNTRY_WORDS.search(searchable))
        sports_match = bool(SPORT_WORDS.search(searchable))

        # Include every channel in a selected country/sports category.
        # Also catch channels whose own names indicate a country and sport,
        # even when the provider has placed them in a generic category.
        if not category_match and not (country_match and sports_match):
            continue

        records.append({
            "category_id": category_id,
            "category_name": category_name,
            "stream_id": stream_id,
            "channel_name": channel_name,
            "epg_channel_id": str(
                stream.get("epg_channel_id", "") or ""
            ),
            "stream_icon": str(stream.get("stream_icon", "") or ""),
            "country_sports_group_match": category_match,
        })

    records.sort(
        key=lambda item: (
            item["category_name"].casefold(),
            item["channel_name"].casefold(),
            item["stream_id"],
        )
    )

    selected_list = [
        {"category_id": category_id, "category_name": name}
        for category_id, name in sorted(
            selected_categories.items(),
            key=lambda item: item[1].casefold(),
        )
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    # Human-readable master list
    txt_path = OUTPUT_DIR / "mega_sports_master.txt"
    with txt_path.open("w", encoding="utf-8", newline="") as file:
        file.write("MEGA SPORTS MASTER LIST — USA / UK / CANADA\n")
        file.write(f"Generated UTC: {generated_at}\n")
        file.write(f"Categories scanned: {len(category_map)}\n")
        file.write(f"Live streams scanned: {len(streams)}\n")
        file.write(
            f"Country/sports categories selected: {len(selected_categories)}\n"
        )
        file.write(f"Matching channel records: {len(records)}\n\n")
        file.write(
            "CATEGORY ID | CATEGORY NAME | STREAM ID | "
            "CHANNEL NAME | EPG CHANNEL ID\n"
        )
        for record in records:
            file.write(
                f"{record['category_id']} | "
                f"{record['category_name']} | "
                f"{record['stream_id']} | "
                f"{record['channel_name']} | "
                f"{record['epg_channel_id']}\n"
            )

    # CSV for sorting and comparing channel names
    csv_path = OUTPUT_DIR / "mega_sports_master.csv"
    fields = [
        "category_id",
        "category_name",
        "stream_id",
        "channel_name",
        "epg_channel_id",
        "stream_icon",
        "country_sports_group_match",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)

    # JSON retains category information and all matching stream metadata
    json_path = OUTPUT_DIR / "mega_sports_master.json"
    summary = {
        "provider": "MEGA",
        "generated_utc": generated_at,
        "api_host": parsed.hostname,
        "categories_scanned": len(category_map),
        "live_streams_scanned": len(streams),
        "selected_country_sports_categories": selected_list,
        "matching_channel_count": len(records),
        "channels": records,
    }
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)

    print("")
    print("MEGA sports scan completed.")
    print(f"Categories scanned: {len(category_map)}")
    print(f"Live streams scanned: {len(streams)}")
    print(f"Country/sports categories matched: {len(selected_categories)}")
    print(f"Matching channel records: {len(records)}")
    print(f"Created: {txt_path}")
    print(f"Created: {csv_path}")
    print(f"Created: {json_path}")

    if not records:
        print(
            "WARNING: No matches found. Review the provider's category "
            "names and the country/sport matching rules."
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Scan cancelled.")
    except Exception as exc:
        sys.exit(f"ERROR: {exc}")
