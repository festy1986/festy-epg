
#!/usr/bin/env python3
"""
MEGA Sports Master Scanner

Scans the MEGA Xtream API and builds a master reference of sports channels
from USA, UK, and Canada groups.

Outputs:
    config/mega_sports_master.txt
    config/mega_sports_master.csv
    config/mega_sports_master.json

Credentials are read exclusively from GitHub Actions secrets.
"""

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


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

BASE_URL = os.environ.get("MEGA_XTREAM_URL", "").strip().rstrip("/")
USERNAME = os.environ.get("MEGA_XTREAM_USERNAME", "").strip()
PASSWORD = os.environ.get("MEGA_XTREAM_PASSWORD", "")

OUTPUT_DIR = Path("config")
TIMEOUT = (20, 90)
MAX_ATTEMPTS = 4

if not BASE_URL or not USERNAME or not PASSWORD:
    sys.exit(
        "ERROR: One or more MEGA_XTREAM_* secrets are missing. "
        "Credential values will not be printed."
    )

if not re.match(r"^https?://", BASE_URL, re.IGNORECASE):
    BASE_URL = "http://" + BASE_URL

# Support either the server base URL or a URL already ending in
# player_api.php.
if BASE_URL.lower().endswith("/player_api.php"):
    API_URL = BASE_URL
else:
    API_URL = BASE_URL + "/player_api.php"

PARSED_URL = urlparse(API_URL)

if not PARSED_URL.hostname:
    sys.exit("ERROR: MEGA_XTREAM_URL is not a valid server URL.")


# ---------------------------------------------------------------------
# Dynamic name matching
#
# Channel names can change daily. This scanner therefore uses category
# and channel names rather than relying on a hardcoded channel lineup.
# Stream IDs and EPG IDs are retained for later matching.
# ---------------------------------------------------------------------

SPORT_WORDS = re.compile(
    r"\b(?:"
    r"sports?|sport|"
    r"mlb|nba|nfl|nhl|wnba|"
    r"baseball|basketball|football|soccer|hockey|"
    r"rugby|cricket|tennis|golf|"
    r"motorsports?|motor\s*racing|racing|nascar|"
    r"formula\s*1|formula\s*e|\bf1\b|"
    r"boxing|ufc|mma|wwe|wrestling|"
    r"espn|fox\s*sports|sky\s*sports|"
    r"sportsnet|tsn|dazn|"
    r"bein\s*sports|bt\s*sport|tnt\s*sports|"
    r"ppv|pay.per.view|league\s*pass|redzone|"
    r"fight|olympics?|"
    r"college\s*football|college\s*basketball|"
    r"pga|lpga|atp|wta|mls|"
    r"premier\s*league|champions\s*league|"
    r"europa\s*league|fifa|"
    r"rugby\s*league|rugby\s*union"
    r")\b",
    re.IGNORECASE,
)

COUNTRY_WORDS = re.compile(
    r"\b(?:"
    r"USA|U\.S\.A\.?|United\s+States|America|American|"
    r"UK|U\.K\.|United\s+Kingdom|Britain|British|"
    r"Canada|Canadian|CAN"
    r")\b",
    re.IGNORECASE,
)

COUNTRY_PREFIX = re.compile(
    r"^\s*(?:"
    r"US|USA|U\.S\.A\.?|"
    r"UK|U\.K\.|"
    r"CA|CAN|CANADA|"
    r"UNITED\s+STATES|UNITED\s+KINGDOM"
    r")\s*(?::|/|\||_|-|\b)",
    re.IGNORECASE,
)

# Explicit country identifiers also support common group-label styles.
COUNTRY_LABELS = (
    ("USA", re.compile(
        r"\b(?:USA|U\.S\.A\.?|United\s+States|America|American)\b",
        re.IGNORECASE,
    )),
    ("UK", re.compile(
        r"\b(?:UK|U\.K\.|United\s+Kingdom|Britain|British)\b",
        re.IGNORECASE,
    )),
    ("Canada", re.compile(
        r"\b(?:Canada|Canadian|CAN)\b",
        re.IGNORECASE,
    )),
)


def identify_countries(text):
    """Return supported countries indicated by a name or group label."""
    found = []

    for country, pattern in COUNTRY_LABELS:
        if pattern.search(text):
            found.append(country)

    # Prefixes such as US:, UK-, CA| are also supported.
    prefix = COUNTRY_PREFIX.search(text)
    if prefix:
        token = prefix.group(0).strip().rstrip(":/|_- ").upper()

        if token in ("US", "USA", "U.S.A.", "UNITED STATES"):
            if "USA" not in found:
                found.append("USA")
        elif token in ("UK", "U.K.", "UNITED KINGDOM"):
            if "UK" not in found:
                found.append("UK")
        elif token in ("CA", "CAN", "CANADA"):
            if "Canada" not in found:
                found.append("Canada")

    return found


def has_sports_terms(text):
    """Check whether a category or channel name appears sports-related."""
    return bool(SPORT_WORDS.search(text))


# ---------------------------------------------------------------------
# HTTP session and API access
# ---------------------------------------------------------------------

def create_session():
    """Create an HTTP session with retries for temporary connection errors."""
    session = requests.Session()

    retry_policy = Retry(
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

    adapter = HTTPAdapter(
        max_retries=retry_policy,
        pool_connections=2,
        pool_maxsize=2,
    )

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Connection": "close",
    })

    return session


SESSION = create_session()


def api_get(action=None):
    """
    Request the MEGA Xtream API.

    Authentication details are sent as query parameters and are never
    included in printed messages.
    """
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
            except ValueError as exc:
                raise RuntimeError(
                    "MEGA returned a non-JSON API response."
                ) from exc

            if isinstance(data, dict):
                user_info = data.get("user_info", {})

                if isinstance(user_info, dict):
                    auth = str(user_info.get("auth", "1")).lower()

                    if auth in ("0", "false"):
                        raise RuntimeError(
                            "MEGA rejected authentication. "
                            "Check the repository secrets."
                        )

            return data

        except RuntimeError:
            raise

        except requests.exceptions.RequestException as exc:
            last_error = exc

            if attempt < MAX_ATTEMPTS:
                delay = min(2 ** attempt, 15)

                print(
                    f"API request attempt {attempt}/{MAX_ATTEMPTS} failed "
                    f"({type(exc).__name__}); retrying in {delay}s."
                )

                time.sleep(delay)

    raise RuntimeError(
        f"MEGA API request failed after {MAX_ATTEMPTS} attempts "
        f"({type(last_error).__name__}). "
        "Credential values have not been printed."
    )


def require_list(data, description):
    """Validate that an API result is a list."""
    if not isinstance(data, list):
        raise RuntimeError(
            f"MEGA did not return a valid {description} list."
        )

    return data


# ---------------------------------------------------------------------
# Fetch provider data
# ---------------------------------------------------------------------

def fetch_provider_data():
    print("Connecting to MEGA Xtream API.")
    print(f"API host: {PARSED_URL.hostname}")
    print("Credentials are hidden.")

    # This is the same account-information request that was confirmed
    # working with the supplied MEGA API endpoint.
    account = api_get()

    if not isinstance(account, dict):
        raise RuntimeError(
            "Unexpected response from the MEGA account endpoint."
        )

    user_info = account.get("user_info", {})

    if not isinstance(user_info, dict):
        raise RuntimeError(
            "MEGA account response did not contain user information."
        )

    if str(user_info.get("auth", "0")) != "1":
        raise RuntimeError(
            "MEGA authentication was not confirmed."
        )

    status = str(user_info.get("status", "")).strip()

    if status and status.lower() != "active":
        raise RuntimeError(
            f"MEGA account is not active (status: {status})."
        )

    print("MEGA authentication confirmed.")

    categories = require_list(
        api_get("get_live_categories"),
        "live-category",
    )
    print(f"Live categories received: {len(categories)}")

    streams = require_list(
        api_get("get_live_streams"),
        "live-stream",
    )
    print(f"Live streams received: {len(streams)}")

    return categories, streams


# ---------------------------------------------------------------------
# Build category map and master channel list
# ---------------------------------------------------------------------

def build_category_map(categories):
    category_map = {}

    for category in categories:
        if not isinstance(category, dict):
            continue

        category_id = str(
            category.get("category_id", "")
        ).strip()

        category_name = str(
            category.get("category_name", "")
        ).strip()

        if category_id:
            category_map[category_id] = (
                category_name or "(unnamed category)"
            )

    return category_map


def build_master_list(categories, streams):
    category_map = build_category_map(categories)

    # Select categories explicitly identified as sports categories in
    # USA, UK, or Canada. Names are evaluated at runtime because they
    # can change at the provider.
    selected_categories = {}

    for category_id, category_name in category_map.items():
        countries = identify_countries(category_name)

        if countries and has_sports_terms(category_name):
            selected_categories[category_id] = {
                "category_id": category_id,
                "category_name": category_name,
                "countries": countries,
            }

    records = []
    seen = set()

    for stream in streams:
        if not isinstance(stream, dict):
            continue

        stream_id = str(
            stream.get("stream_id", stream.get("id", ""))
        ).strip()

        channel_name = str(
            stream.get("name", stream.get("stream_name", ""))
        ).strip()

        category_id = str(
            stream.get("category_id", "")
        ).strip()

        if not channel_name:
            continue

        category_name = category_map.get(
            category_id,
            "(unknown category)",
        )

        category_match = category_id in selected_categories

        combined_name = f"{category_name} {channel_name}"
        countries = identify_countries(combined_name)
        sports_match = has_sports_terms(combined_name)

        # Include every stream in a selected sports category.
        # Also catch a sports channel with a country identifier in its
        # own name when the provider has put it in a generic category.
        fallback_match = bool(countries and sports_match)

        if not category_match and not fallback_match:
            continue

        if category_match:
            countries = selected_categories[category_id]["countries"]

        # Avoid duplicate API records while preserving differently
        # named entries and different stream IDs.
        unique_key = (
            category_id,
            stream_id,
            channel_name.casefold(),
        )

        if unique_key in seen:
            continue

        seen.add(unique_key)

        records.append({
            "country": ", ".join(countries),
            "category_id": category_id,
            "category_name": category_name,
            "stream_id": stream_id,
            "channel_name": channel_name,
            "epg_channel_id": str(
                stream.get("epg_channel_id", "") or ""
            ),
            "stream_icon": str(
                stream.get("stream_icon", "") or ""
            ),
            "category_match": category_match,
            "country_sports_name_match": fallback_match,
        })

    records.sort(
        key=lambda record: (
            record["country"].casefold(),
            record["category_name"].casefold(),
            record["channel_name"].casefold(),
            record["stream_id"],
        )
    )

    selected_list = sorted(
        selected_categories.values(),
        key=lambda category: (
            category["category_name"].casefold(),
            category["category_id"],
        ),
    )

    return category_map, selected_list, records


# ---------------------------------------------------------------------
# Write files directly into config/
# ---------------------------------------------------------------------

def write_master_files(category_map, selected_categories, streams, records):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc).isoformat()

    txt_path = OUTPUT_DIR / "mega_sports_master.txt"
    csv_path = OUTPUT_DIR / "mega_sports_master.csv"
    json_path = OUTPUT_DIR / "mega_sports_master.json"

    # Plain-text list
    with txt_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        file.write("MEGA SPORTS MASTER LIST — USA / UK / CANADA\n")
        file.write(f"Generated UTC: {generated_at}\n")
        file.write(f"API host: {PARSED_URL.hostname}\n")
        file.write(f"Categories scanned: {len(category_map)}\n")
        file.write(f"Live streams scanned: {len(streams)}\n")
        file.write(
            f"Selected country/sports categories: "
            f"{len(selected_categories)}\n"
        )
        file.write(f"Matching channel records: {len(records)}\n\n")

        file.write(
            "COUNTRY | CATEGORY ID | CATEGORY NAME | STREAM ID | "
            "CHANNEL NAME | EPG CHANNEL ID\n"
        )

        for record in records:
            file.write(
                f"{record['country']} | "
                f"{record['category_id']} | "
                f"{record['category_name']} | "
                f"{record['stream_id']} | "
                f"{record['channel_name']} | "
                f"{record['epg_channel_id']}\n"
            )

    # CSV list for sorting, filtering, and matching
    csv_fields = [
        "country",
        "category_id",
        "category_name",
        "stream_id",
        "channel_name",
        "epg_channel_id",
        "stream_icon",
        "category_match",
        "country_sports_name_match",
    ]

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=csv_fields,
        )
        writer.writeheader()
        writer.writerows(records)

    # JSON data for automated cross-referencing in sports-2
    summary = {
        "provider": "MEGA",
        "generated_utc": generated_at,
        "api_host": PARSED_URL.hostname,
        "scope": ["USA", "UK", "Canada"],
        "categories_scanned": len(category_map),
        "live_streams_scanned": len(streams),
        "selected_country_sports_categories": selected_categories,
        "matching_channel_count": len(records),
        "channels": records,
    }

    with json_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            summary,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    return txt_path, csv_path, json_path


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    categories, streams = fetch_provider_data()

    category_map, selected_categories, records = build_master_list(
        categories,
        streams,
    )

    txt_path, csv_path, json_path = write_master_files(
        category_map,
        selected_categories,
        streams,
        records,
    )

    print("")
    print("MEGA SPORTS MASTER SCAN COMPLETED")
    print("---------------------------------")
    print(f"Categories scanned: {len(category_map)}")
    print(f"Live streams scanned: {len(streams)}")
    print(f"Country/sports categories matched: {len(selected_categories)}")
    print(f"Matching channel records: {len(records)}")
    print("")
    print("Master files created:")
    print(f"  {txt_path}")
    print(f"  {csv_path}")
    print(f"  {json_path}")

    if not records:
        print(
            "WARNING: No matching channels were found. "
            "Review the provider's category naming."
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Scan cancelled.")
    except Exception as exc:
        sys.exit(f"ERROR: {exc}")
