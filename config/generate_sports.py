import os
import requests
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import re
import html
import time
from urllib.parse import quote
from difflib import SequenceMatcher


CHANNEL_FILE = "config/sports_channels.txt"
TEAM_FILE = "config/sports_teams.txt"
OUTPUT_FILE = "guides/sports.xml"
SPORTS_LOGO_ROOT = "sports-logos"
SUPPORTED_MAJOR_LEAGUES = ("MLB", "NBA", "NFL", "NHL")


GITHUB_RAW_ROOT = (
    "https://raw.githubusercontent.com/"
    "festy1986/festy-epg/main/"
)


REDZONE_LOGO_URL = (
    GITHUB_RAW_ROOT
    + "logos/redzone.png"
)


XTREAM_URL = os.environ["XTREAM_URL"].rstrip("/")
USERNAME = os.environ["XTREAM_USERNAME"]
PASSWORD = os.environ["XTREAM_PASSWORD"]


# --------------------------------------------------
# Normalize Xtream URL
# --------------------------------------------------

if XTREAM_URL.startswith("https://"):

    XTREAM_URL = XTREAM_URL.replace(
        "https://",
        "http://",
        1
    )


if ":80" not in XTREAM_URL and ":443" not in XTREAM_URL:

    XTREAM_URL += ":80"


os.makedirs(
    "guides",
    exist_ok=True
)


# --------------------------------------------------
# HTTP session
# --------------------------------------------------

session = requests.Session()

session.headers.update(
    {
        "User-Agent": "Mozilla/5.0"
    }
)


# --------------------------------------------------
# Statistics
# --------------------------------------------------

public_api_lookups = 0

public_api_matches = 0

verified_public_times_used = 0

no_public_match = 0

logos_found = 0

logos_missing = 0

team_name_conversions = 0


# --------------------------------------------------
# Detailed diagnostics
# --------------------------------------------------

debug_stats = {

    "provider_event_extracted": 0,

    "provider_event_failed": 0,

    "provider_matchup_parts_failed": 0,

    "canonical_team_matches": 0,

    "canonical_team_failures": 0,

    "logo_typo_corrections": 0,

    "logo_typo_correction_failures": 0,

    "public_events_downloaded": 0,

    "public_events_empty": 0,

    "public_events_team_match_failed": 0,

    "public_events_date_time_failed": 0,

    "public_events_success": 0,

    "logo_direct_order_found": 0,

    "logo_reverse_order_found": 0,

    "logo_not_found": 0,

    "dynamic_major_channels_added": 0,

    "single_team_logo_found": 0,

    "single_team_logo_missing": 0,

    "provider_name_fallback_used": 0,

}


# --------------------------------------------------
# Text cleanup
# --------------------------------------------------

def clean_text(text):

    if not text:

        return ""


    text = html.unescape(
        str(text)
    )


    text = re.sub(
        r"<[^>]+>",
        "",
        text
    )


    text = text.replace(
        "\n",
        " "
    )


    text = text.replace(
        "\r",
        " "
    )


    text = re.sub(
        r"\s+",
        " ",
        text
    )


    return text.strip()


# --------------------------------------------------
# Normalize matchup separator
# --------------------------------------------------

def normalize_matchup(text):

    if not text:

        return ""


    text = clean_text(
        text
    )


    text = re.sub(
        r"\s+[xX]\s+",
        " vs. ",
        text
    )


    text = re.sub(
        r"\s+@\s+",
        " vs. ",
        text
    )


    text = re.sub(
        r"\s+at\s+",
        " vs. ",
        text,
        flags=re.IGNORECASE
    )


    text = re.sub(
        r"\s+v\.?\s+",
        " vs. ",
        text,
        flags=re.IGNORECASE
    )


    text = re.sub(
        r"\s+vs\s*\.?\s+",
        " vs. ",
        text,
        flags=re.IGNORECASE
    )


    text = re.sub(
        r"\s+",
        " ",
        text
    )


    return text.strip(
        " -|:;"
    )


# --------------------------------------------------
# Clean provider channel/event text for fallback use.
#
# This does not replace the existing matchup parser.
# It is used only when a normal A-vs-B matchup cannot
# be extracted, such as boxing, PPV, or named events.
# --------------------------------------------------

def extract_provider_event_text(text):

    if not text:

        return ""


    text = clean_text(
        text
    )


    if "|" in text:

        text = text.split(
            "|",
            1
        )[1]


    text = re.split(
        r"\bstart\s*[:=]",
        text,
        maxsplit=1,
        flags=re.IGNORECASE
    )[0]


    text = re.split(
        r"\bstop\s*[:=]",
        text,
        maxsplit=1,
        flags=re.IGNORECASE
    )[0]


    text = re.sub(
        r"^(?:MLB|NBA|NFL|NHL)\s*[-:]?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )


    text = re.sub(
        r"^(?:PPV\s+)?EVENT\s*\d*\s*[:|-]?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )


    text = normalize_matchup(
        text
    )


    return clean_text(
        text
    )


# --------------------------------------------------
# Extract matchup from provider channel name
#
# MLB / NBA / NFL / NHL FIX:
# - Keep the original separator parser first.
# - If it cannot cleanly identify two teams, scan the
#   whole channel name against sports_teams.txt aliases.
# - Longest known aliases win.
# - This fallback is ONLY for MLB/NBA/NFL/NHL.
# - All other sports/events keep the original behavior.
# --------------------------------------------------

def extract_provider_matchup(text):

    if not text:

        debug_stats[
            "provider_event_failed"
        ] += 1

        return ""


    original_text = clean_text(
        text
    )


    league_hint = detect_league(
        original_text
    )


    text = original_text


    # Remove provider scheduling metadata first.
    text = re.split(
        r"\bstart\s*[:=]",
        text,
        maxsplit=1,
        flags=re.IGNORECASE
    )[0]


    text = re.split(
        r"\bstop\s*[:=]",
        text,
        maxsplit=1,
        flags=re.IGNORECASE
    )[0]


    # Existing pipe-based names remain supported.
    if "|" in text:

        text = text.split(
            "|",
            1
        )[1]

    else:

        # Also support current provider names such as:
        # NBA 02: Knicks (NYK) x Timberwolves (MIN)
        # MLB 04 - Blue Jays x Red Sox
        text = re.sub(
            r"^(?:US\s*:\s*)?"
            r"(?:MLB|NBA|NFL|NHL)"
            r"(?:\s+(?:CHANNEL\s*)?\d+)?"
            r"\s*[-:|]\s*",
            "",
            text,
            flags=re.IGNORECASE
        )


    # Remove provider slot/date/time prefixes such as:
    # 02 - 8/13 7pm Packers at Steelers
    text = re.sub(
        r"^\s*\d+\s*[-:]\s*",
        "",
        text
    )


    text = re.sub(
        r"^\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*",
        "",
        text
    )


    text = re.sub(
        r"^\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\s*",
        "",
        text,
        flags=re.IGNORECASE
    )


    # Remove provider abbreviations such as (NYK), (MIN), etc.
    # The canonical team alias lookup supplies official names.
    text = re.sub(
        r"\s*\([A-Za-z0-9]{2,5}\)\s*",
        " ",
        text
    )


    text = clean_text(
        text
    )


    # Original path first.
    matchup = normalize_matchup(
        text
    )


    parts = matchup_parts(
        matchup
    )


    if len(parts) == 2:

        debug_stats[
            "provider_event_extracted"
        ] += 1

        return matchup


    # Major-league-only fallback.
    if league_hint not in SUPPORTED_MAJOR_LEAGUES:

        debug_stats[
            "provider_event_failed"
        ] += 1

        return ""


    normalized_source = normalize_team_name(
        original_text
    )


    if not normalized_source:

        debug_stats[
            "provider_event_failed"
        ] += 1

        return ""


    alias_hits = []


    # Longest aliases first so a specific full/nickname alias
    # wins over a shorter overlapping alias.
    sorted_aliases = sorted(
        team_aliases[
            league_hint
        ].items(),
        key=lambda item: len(item[0]),
        reverse=True
    )


    for normalized_alias, official_name in sorted_aliases:

        if not normalized_alias:

            continue


        pattern = (
            r"(?<![a-z0-9])"
            + re.escape(
                normalized_alias
            )
            + r"(?![a-z0-9])"
        )


        for match in re.finditer(
            pattern,
            normalized_source
        ):

            alias_hits.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "alias": normalized_alias,
                    "official": official_name
                }
            )


    if not alias_hits:

        debug_stats[
            "provider_event_failed"
        ] += 1

        return ""


    alias_hits.sort(
        key=lambda hit: (
            hit["start"],
            -(
                hit["end"]
                - hit["start"]
            )
        )
    )


    filtered_hits = []


    for hit in alias_hits:

        overlaps = False


        for kept in filtered_hits:

            if not (
                hit["end"] <= kept["start"]
                or hit["start"] >= kept["end"]
            ):

                overlaps = True
                break


        if not overlaps:

            filtered_hits.append(
                hit
            )


    ordered_teams = []


    for hit in sorted(
        filtered_hits,
        key=lambda value: value["start"]
    ):

        official_name = hit[
            "official"
        ]


        if official_name in ordered_teams:

            continue


        ordered_teams.append(
            official_name
        )


    if len(ordered_teams) != 2:

        debug_stats[
            "provider_event_failed"
        ] += 1

        return ""


    matchup = (
        f"{ordered_teams[0]}"
        f" vs. "
        f"{ordered_teams[1]}"
    )


    print()

    print(
        "[MAJOR LEAGUE TEAM SCAN]"
    )


    print(
        f"  Provider name: {original_text}"
    )


    print(
        f"  Identified matchup: {matchup}"
    )


    debug_stats[
        "provider_event_extracted"
    ] += 1


    return matchup


# --------------------------------------------------
# Extract flexible provider date/time clues
#
# IMPORTANT:
# Provider channel names are human-maintained and may use
# different formats and time zones from one update to the next.
#
# These clues are NEVER authoritative guide times. They are
# only fingerprints used to identify the correct ESPN event.
# ESPN remains the final authoritative time source.
# --------------------------------------------------

def extract_start_datetime(text):

    if not text:

        return None


    text = clean_text(
        text
    )


    patterns = [

        r"start\s*:\s*"
        r"(\d{4}-\d{2}-\d{2})"
        r"\s+"
        r"(\d{2}:\d{2}(?::\d{2})?)",

        r"start\s*=\s*"
        r"(\d{4}-\d{2}-\d{2})"
        r"\s+"
        r"(\d{2}:\d{2}(?::\d{2})?)",

        r"(\d{4}-\d{2}-\d{2})"
        r"\s+"
        r"(\d{2}:\d{2}:\d{2})"

    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )


        if not match:

            continue


        date_part = match.group(
            1
        )


        time_part = match.group(
            2
        )


        if len(time_part) == 5:

            time_part += ":00"


        try:

            return datetime.strptime(
                f"{date_part} "
                f"{time_part}",
                "%Y-%m-%d %H:%M:%S"
            )


        except ValueError:

            continue


    return None


def extract_provider_date_hint(text):

    if not text:

        return None


    text = clean_text(
        text
    )


    eastern_now = datetime.now(
        ZoneInfo(
            "America/New_York"
        )
    )


    # Numeric M/D[/Y].
    match = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b",
        text
    )


    if match:

        month = int(
            match.group(1)
        )

        day = int(
            match.group(2)
        )

        year_text = match.group(
            3
        )


        if year_text:

            year = int(
                year_text
            )

            if year < 100:

                year += 2000

            try:

                return datetime(
                    year,
                    month,
                    day
                ).date()

            except ValueError:

                pass


        else:

            candidates = []

            for year in (
                eastern_now.year - 1,
                eastern_now.year,
                eastern_now.year + 1
            ):

                try:

                    candidates.append(
                        datetime(
                            year,
                            month,
                            day
                        ).date()
                    )

                except ValueError:

                    continue


            if candidates:

                return min(
                    candidates,
                    key=lambda candidate: abs(
                        (
                            candidate
                            - eastern_now.date()
                        ).days
                    )
                )


    # Month-name forms:
    # Aug 29, Aug 29 2026, August 29th 2026,
    # 29 Aug, 29 August 2026.
    month_lookup = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12
    }


    month_first = re.search(
        r"\b("
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|"
        r"Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
        r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?"
        r")\.?\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?"
        r"(?:,\s*|\s+)?"
        r"(\d{4})?\b",
        text,
        flags=re.IGNORECASE
    )


    day_first = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"("
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|"
        r"Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
        r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?"
        r")\.?"
        r"(?:,\s*|\s+)?"
        r"(\d{4})?\b",
        text,
        flags=re.IGNORECASE
    )


    month = None
    day = None
    year_text = None


    if month_first:

        month = month_lookup.get(
            month_first.group(1).lower()
        )

        day = int(
            month_first.group(2)
        )

        year_text = month_first.group(
            3
        )


    elif day_first:

        day = int(
            day_first.group(1)
        )

        month = month_lookup.get(
            day_first.group(2).lower()
        )

        year_text = day_first.group(
            3
        )


    if month and day:

        if year_text:

            years = [
                int(year_text)
            ]

        else:

            years = [
                eastern_now.year - 1,
                eastern_now.year,
                eastern_now.year + 1
            ]


        candidates = []

        for year in years:

            try:

                candidates.append(
                    datetime(
                        year,
                        month,
                        day
                    ).date()
                )

            except ValueError:

                continue


        if candidates:

            return min(
                candidates,
                key=lambda candidate: abs(
                    (
                        candidate
                        - eastern_now.date()
                    ).days
                )
            )


    return None


def extract_provider_game_number(text):

    if not text:

        return None


    text = clean_text(
        text
    )


    patterns = [

        r"\bgame\s*#?\s*(\d{1,2})\b",

        r"\bg\s*#?\s*(\d{1,2})\b",

        r"\b(?:doubleheader|dh)\s*"
        r"(?:game\s*)?#?\s*(\d{1,2})\b"

    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )


        if match:

            try:

                number = int(
                    match.group(1)
                )

            except ValueError:

                continue


            if number >= 1:

                return number


    return None


def extract_explicit_timezone(text):

    if not text:

        return None


    text = clean_text(
        text
    )


    # Prefer explicit IANA names if the provider ever supplies one.
    iana_match = re.search(
        r"\b([A-Za-z]+/[A-Za-z_+-]+)\b",
        text
    )


    if iana_match:

        zone_name = iana_match.group(
            1
        )

        try:

            ZoneInfo(
                zone_name
            )

            return zone_name

        except Exception:

            pass


    timezone_aliases = {

        "ET": "America/New_York",
        "EST": "America/New_York",
        "EDT": "America/New_York",

        "CT": "America/Chicago",
        "CST": "America/Chicago",
        "CDT": "America/Chicago",

        "MT": "America/Denver",
        "MST": "America/Denver",
        "MDT": "America/Denver",

        "PT": "America/Los_Angeles",
        "PST": "America/Los_Angeles",
        "PDT": "America/Los_Angeles",

        "GMT": "UTC",
        "UTC": "UTC",
        "BST": "Europe/London",
        "CET": "Europe/Paris",
        "CEST": "Europe/Paris"

    }


    matches = re.findall(
        r"\b(?:ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|"
        r"PT|PST|PDT|GMT|UTC|BST|CET|CEST)\b",
        text,
        flags=re.IGNORECASE
    )


    if matches:

        return timezone_aliases.get(
            matches[-1].upper()
        )


    return None


def extract_provider_time_clues(
    text,
    provider_timezone
):

    if not text:

        return []


    text = clean_text(
        text
    )


    eastern_zone = ZoneInfo(
        "America/New_York"
    )


    provider_date = extract_provider_date_hint(
        text
    )


    explicit_timezone = extract_explicit_timezone(
        text
    )


    clues = []


    def add_clue(
        naive_datetime,
        zone_name,
        source,
        confidence
    ):

        if not naive_datetime:

            return


        try:

            source_zone = ZoneInfo(
                zone_name
            )

        except Exception:

            source_zone = timezone.utc


        aware_datetime = naive_datetime.replace(
            tzinfo=source_zone
        )


        eastern_datetime = aware_datetime.astimezone(
            eastern_zone
        )


        key = (
            eastern_datetime.isoformat(),
            source
        )


        for existing in clues:

            if (
                existing["datetime"].isoformat(),
                existing["source"]
            ) == key:

                return


        clues.append(
            {
                "datetime": eastern_datetime,
                "source": source,
                "confidence": confidence
            }
        )


    # 1. Structured provider start timestamp.
    #
    # These have historically been provider-local server time,
    # so the provider timezone is the primary interpretation.
    structured_start = extract_start_datetime(
        text
    )


    if structured_start:

        add_clue(
            structured_start,
            provider_timezone,
            "structured provider start",
            100
        )


        # Safety interpretation only. If a human has embedded an
        # ISO-looking timestamp that is actually Eastern, ESPN can
        # still identify the correct event from this alternative.
        add_clue(
            structured_start,
            "America/New_York",
            "structured start interpreted as Eastern",
            55
        )


    # 2. Human-entered clock times.
    #
    # Accept:
    #   7 PM
    #   7:00 PM
    #   7.15pm
    #   19:00
    #   19.15
    #
    # We intentionally generate more than one plausible timezone
    # interpretation when no explicit timezone is present. ESPN,
    # not the provider text, decides which event is the real one.
    time_matches = []


    twelve_hour_pattern = re.compile(
        r"(?<!\d)"
        r"(\d{1,2})"
        r"(?:[:.](\d{2}))?"
        r"\s*"
        r"(a\.?m\.?|p\.?m\.?)"
        r"(?![A-Za-z])",
        flags=re.IGNORECASE
    )


    for match in twelve_hour_pattern.finditer(
        text
    ):

        hour = int(
            match.group(1)
        )

        minute = int(
            match.group(2)
            or 0
        )

        meridiem = re.sub(
            r"[^apm]",
            "",
            match.group(3).lower()
        )


        if hour < 1 or hour > 12 or minute > 59:

            continue


        if meridiem.startswith(
            "p"
        ) and hour != 12:

            hour += 12


        if meridiem.startswith(
            "a"
        ) and hour == 12:

            hour = 0


        time_matches.append(
            (
                match.start(),
                hour,
                minute,
                "12-hour"
            )
        )


    twenty_four_pattern = re.compile(
        r"(?<![\d:/.-])"
        r"([01]?\d|2[0-3])"
        r"[:.]"
        r"([0-5]\d)"
        r"(?!\s*(?:a\.?m\.?|p\.?m\.?))"
        r"(?![\d:/.-])",
        flags=re.IGNORECASE
    )


    for match in twenty_four_pattern.finditer(
        text
    ):

        time_matches.append(
            (
                match.start(),
                int(match.group(1)),
                int(match.group(2)),
                "24-hour"
            )
        )


    # Avoid treating the time inside a structured start timestamp
    # as an unrelated human clue when we already parsed that field.
    structured_span = None

    structured_match = re.search(
        r"\bstart\s*[:=]\s*"
        r"\d{4}-\d{2}-\d{2}\s+"
        r"\d{1,2}:\d{2}(?::\d{2})?",
        text,
        flags=re.IGNORECASE
    )


    if structured_match:

        structured_span = (
            structured_match.start(),
            structured_match.end()
        )


    seen_clock_values = set()


    for position, hour, minute, format_name in sorted(
        time_matches,
        key=lambda value: value[0]
    ):

        if (
            structured_span
            and structured_span[0] <= position < structured_span[1]
        ):

            continue


        clock_key = (
            hour,
            minute
        )


        if clock_key in seen_clock_values:

            continue


        seen_clock_values.add(
            clock_key
        )


        if provider_date:

            candidate_dates = [
                provider_date
            ]

        elif structured_start:

            candidate_dates = [
                structured_start.date()
            ]

        else:

            eastern_now = datetime.now(
                eastern_zone
            )

            candidate_dates = [
                eastern_now.date()
            ]


        for candidate_date in candidate_dates:

            naive_datetime = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                minute
            )


            if explicit_timezone:

                add_clue(
                    naive_datetime,
                    explicit_timezone,
                    f"human {format_name} explicit timezone",
                    95
                )


            else:

                # Human channel labels from this provider have not
                # been consistent about timezone. Preserve both the
                # provider-local and Eastern interpretations and let
                # ESPN event proximity resolve the ambiguity.
                add_clue(
                    naive_datetime,
                    provider_timezone,
                    f"human {format_name} provider-zone interpretation",
                    70
                )


                add_clue(
                    naive_datetime,
                    "America/New_York",
                    f"human {format_name} Eastern interpretation",
                    75
                )


                add_clue(
                    naive_datetime,
                    "UTC",
                    f"human {format_name} UTC interpretation",
                    45
                )


    clues.sort(
        key=lambda clue: (
            -clue["confidence"],
            clue["datetime"]
        )
    )


    return clues


# --------------------------------------------------
# Get provider timezone
# --------------------------------------------------

def get_server_timezone():

    url = (

        f"{XTREAM_URL}/player_api.php"

        f"?username={USERNAME}"

        f"&password={PASSWORD}"

    )


    response = session.get(
        url,
        timeout=60
    )


    response.raise_for_status()


    data = response.json()


    server_info = data.get(
        "server_info",
        {}
    )


    timezone_name = server_info.get(
        "timezone"
    )


    if not timezone_name:

        print(
            "Provider did not return a timezone."
        )


        print(
            "Using UTC."
        )


        return "UTC"


    print(
        f"Provider timezone: "
        f"{timezone_name}"
    )


    return timezone_name


# --------------------------------------------------
# Convert provider time to Eastern Time
# --------------------------------------------------

def convert_to_eastern(
    naive_datetime,
    provider_timezone
):

    if not naive_datetime:

        return None


    try:

        source_zone = ZoneInfo(
            provider_timezone
        )


    except Exception:

        print(
            f"Invalid provider timezone: "
            f"{provider_timezone}"
        )


        print(
            "Falling back to UTC."
        )


        source_zone = timezone.utc


    eastern_zone = ZoneInfo(
        "America/New_York"
    )


    source_datetime = naive_datetime.replace(
        tzinfo=source_zone
    )


    return source_datetime.astimezone(
        eastern_zone
    )


# --------------------------------------------------
# Load requested channels
# --------------------------------------------------

wanted = {}


with open(
    CHANNEL_FILE,
    "r",
    encoding="utf-8"
) as f:

    for line in f:

        line = line.strip()


        if not line:

            continue


        parts = [

            x.strip()

            for x in line.split("|")

        ]


        if len(parts) < 2:

            continue


        channel_id = parts[0]


        display_name = " ".join(
            parts[1:]
        )


        wanted[channel_id] = display_name


print(
    f"Requested channels: "
    f"{len(wanted)}"
)


# --------------------------------------------------
# Load provider timezone
# --------------------------------------------------

provider_timezone = get_server_timezone()


# --------------------------------------------------
# Download provider channels
# --------------------------------------------------

print(
    "Downloading provider channels..."
)


url = (

    f"{XTREAM_URL}/player_api.php"

    f"?username={USERNAME}"

    f"&password={PASSWORD}"

    f"&action=get_live_streams"

)


streams = None


for attempt in range(
    1,
    6
):

    try:

        print(
            f"Downloading provider channels "
            f"(attempt {attempt}/5)..."
        )


        response = session.get(
            url,
            timeout=(30, 600)
        )


        response.raise_for_status()


        streams = response.json()


        break


    except Exception as e:

        print(
            "Download failed:"
        )


        print(
            e
        )


        if attempt < 5:

            time.sleep(
                10
            )


if streams is None:

    print(
        "Unable to download provider channels."
    )


    raise SystemExit(
        1
    )


print(
    f"Provider channels: "
    f"{len(streams)}"
)


provider = {}


for stream in streams:

    stream_id = str(
        stream.get(
            "stream_id"
        )
    )


    provider[stream_id] = stream


# --------------------------------------------------
# Download provider live categories.
#
# The original sports_channels.txt entries are kept.
# This only adds current MLB/NBA/NFL/NHL streams found
# by live category/group name or current channel name.
# --------------------------------------------------

category_names = {}


categories_url = (

    f"{XTREAM_URL}/player_api.php"

    f"?username={USERNAME}"

    f"&password={PASSWORD}"

    f"&action=get_live_categories"

)


try:

    categories_response = session.get(
        categories_url,
        timeout=(30, 120)
    )


    categories_response.raise_for_status()


    categories = categories_response.json()


    for category in categories:

        category_id = str(
            category.get(
                "category_id",
                ""
            )
        )


        category_name = clean_text(
            category.get(
                "category_name",
                ""
            )
        )


        if category_id:

            category_names[
                category_id
            ] = category_name


    print(
        f"Provider live categories: "
        f"{len(category_names)}"
    )


except Exception as e:

    print(
        "Unable to download provider live categories."
    )


    print(
        e
    )


# --------------------------------------------------
# Dynamically add all current major-league streams.
#
# XMLTV IDs remain the current provider stream IDs,
# preserving TiviMate matching.
# --------------------------------------------------

dynamic_major_channels_added = 0


for stream in streams:

    stream_id = str(
        stream.get(
            "stream_id",
            ""
        )
    )


    stream_name = clean_text(
        stream.get(
            "name",
            ""
        )
    )


    category_id = str(
        stream.get(
            "category_id",
            ""
        )
    )


    category_name = category_names.get(
        category_id,
        ""
    )


    detection_text = (

        f"{category_name} "

        f"{stream_name}"

    )


    if not re.search(
        r"\b(?:MLB|NBA|NFL|NHL)\b",
        detection_text,
        flags=re.IGNORECASE
    ):

        continue


    if stream_id not in wanted:

        wanted[
            stream_id
        ] = stream_name


        dynamic_major_channels_added += 1


debug_stats[
    "dynamic_major_channels_added"
] = dynamic_major_channels_added


print(
    f"Dynamic major-league channels added: "
    f"{dynamic_major_channels_added}"
)


print(
    f"Total sports channels selected: "
    f"{len(wanted)}"
)


# --------------------------------------------------
# Load canonical team names and aliases
# --------------------------------------------------

team_aliases = {

    "MLB": {},

    "NBA": {},

    "NFL": {},

    "NHL": {}

}


def normalize_team_name(text):

    if not text:

        return ""


    text = clean_text(
        text
    ).lower()


    text = text.replace(
        "&",
        " and "
    )


    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text
    )


    text = re.sub(
        r"\s+",
        " ",
        text
    )


    return text.strip()


def load_canonical_teams():

    print(
        "Loading canonical team names..."
    )


    current_league = None


    with open(
        TEAM_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        for line_number, raw_line in enumerate(
            f,
            start=1
        ):

            line = raw_line.strip()


            if not line:

                continue


            if line.startswith(
                "#"
            ):

                continue


            if (

                line.startswith("[")

                and

                line.endswith("]")

            ):

                league = line[
                    1:-1
                ].strip().upper()


                if league not in team_aliases:

                    print(
                        f"Unknown league "
                        f"at line "
                        f"{line_number}: "
                        f"{league}"
                    )


                    current_league = None


                else:

                    current_league = league


                continue


            if not current_league:

                continue


            if "=" not in line:

                print(
                    f"Invalid team line "
                    f"{line_number}: "
                    f"{line}"
                )


                continue


            official_name, aliases_text = line.split(
                "=",
                1
            )


            official_name = clean_text(
                official_name
            )


            aliases = [

                clean_text(alias)

                for alias in aliases_text.split(",")

            ]


            aliases.append(
                official_name
            )


            official_normalized = normalize_team_name(
                official_name
            )


            if not official_normalized:

                continue


            for alias in aliases:

                normalized_alias = normalize_team_name(
                    alias
                )


                if not normalized_alias:

                    continue


                team_aliases[
                    current_league
                ][
                    normalized_alias
                ] = official_name


    for league, aliases in team_aliases.items():

        print(
            f"{league}: "
            f"{len(aliases)} "
            f"team aliases loaded"
        )


load_canonical_teams()


# --------------------------------------------------
# Extract matchup parts
# --------------------------------------------------

def matchup_parts(text):

    if not text:

        return []


    text = clean_text(
        text
    )


    match = re.search(

        r"(.+?)\s+"
        r"(?:vs\.?|v\.?|x|@)"
        r"\s+(.+)",

        text,

        flags=re.IGNORECASE

    )


    if not match:

        debug_stats[
            "provider_matchup_parts_failed"
        ] += 1


        return []


    first = clean_text(
        match.group(1)
    )


    second = clean_text(
        match.group(2)
    )


    if not first or not second:

        debug_stats[
            "provider_matchup_parts_failed"
        ] += 1


        return []


    return [

        first,

        second

    ]


# --------------------------------------------------
# Determine league from provider channel name
# --------------------------------------------------

def detect_league(text):

    if not text:

        return None


    text = clean_text(
        text
    )


    match = re.search(

        r"\b(MLB|NBA|NFL|NHL)\b",

        text,

        flags=re.IGNORECASE

    )


    if not match:

        return None


    return match.group(
        1
    ).upper()


# --------------------------------------------------
# Convert provider team name to official name
# --------------------------------------------------

def canonicalize_team_name(
    provider_team,
    league_hint=None
):

    global team_name_conversions


    provider_team = clean_text(
        provider_team
    )


    if not provider_team:

        debug_stats[
            "canonical_team_failures"
        ] += 1


        return provider_team


    normalized_provider = normalize_team_name(
        provider_team
    )


    if not normalized_provider:

        debug_stats[
            "canonical_team_failures"
        ] += 1


        return provider_team


    if league_hint in team_aliases:

        exact = team_aliases[
            league_hint
        ].get(
            normalized_provider
        )


        if exact:

            if exact != provider_team:

                team_name_conversions += 1


            debug_stats[
                "canonical_team_matches"
            ] += 1


            return exact


    if league_hint in team_aliases:

        provider_words = set(
            normalized_provider.split()
        )


        best_match = None

        best_score = 0


        for normalized_alias, official_name in team_aliases[
            league_hint
        ].items():

            alias_words = set(
                normalized_alias.split()
            )


            if not alias_words:

                continue


            if normalized_provider in normalized_alias:

                score = len(
                    normalized_provider
                )


                if score > best_score:

                    best_score = score

                    best_match = official_name


            elif normalized_alias in normalized_provider:

                score = len(
                    normalized_alias
                )


                if score > best_score:

                    best_score = score

                    best_match = official_name


            else:

                shared_words = (

                    provider_words

                    &

                    alias_words

                )


                if shared_words:

                    score = len(
                        shared_words
                    )


                    if score > best_score:

                        best_score = score

                        best_match = official_name


        if best_match:

            team_name_conversions += 1


            debug_stats[
                "canonical_team_matches"
            ] += 1


            return best_match


    debug_stats[
        "canonical_team_failures"
    ] += 1


    return provider_team


# --------------------------------------------------
# Recover a provider team spelling error ONLY after
# the normal matchup logo lookup has failed.
# --------------------------------------------------

def recover_logo_typo_matchup(
    canonical_matchup,
    league_hint,
    preferred_date
):

    if league_hint not in team_aliases:

        return None


    parts = matchup_parts(
        canonical_matchup
    )


    if len(parts) != 2:

        return None


    normalized_parts = [

        normalize_team_name(
            part
        )

        for part in parts

    ]


    recognized = []

    unknown = []


    for index, normalized_part in enumerate(
        normalized_parts
    ):

        if normalized_part in team_aliases[league_hint]:

            recognized.append(index)

        else:

            unknown.append(index)


    if len(recognized) != 1 or len(unknown) != 1:

        return None


    unknown_index = unknown[0]

    provider_team = parts[unknown_index]


    best_score = 0.0

    best_team = None


    for normalized_alias, official_name in team_aliases[
        league_hint
    ].items():

        score = SequenceMatcher(
            None,
            normalized_parts[unknown_index],
            normalized_alias
        ).ratio()


        if score > best_score:

            best_score = score

            best_team = official_name


    if not best_team or best_score < 0.90:

        debug_stats[
            "logo_typo_correction_failures"
        ] += 1

        return None


    corrected_parts = list(parts)

    corrected_parts[unknown_index] = best_team


    candidate_matchup = (

        f"{corrected_parts[0]}"

        f" vs. "

        f"{corrected_parts[1]}"

    )


    print()

    print(
        "[LOGO TYPO RECOVERY]"
    )


    print(
        f"  Provider spelling: {provider_team}"
    )


    print(
        f"  Candidate correction: {best_team}"
    )


    print(
        f"  Similarity: {best_score:.0%}"
    )


    print(
        f"  Proposed matchup: {candidate_matchup}"
    )


    verified_event = find_public_event(
        candidate_matchup,
        preferred_date,
        league_hint
    )


    if not verified_event:

        debug_stats[
            "logo_typo_correction_failures"
        ] += 1


        print(
            "  ESPN did not verify the proposed matchup."
        )


        return None


    debug_stats[
        "logo_typo_corrections"
    ] += 1


    print(
        "  ESPN verified the proposed matchup."
    )


    return candidate_matchup, verified_event


# --------------------------------------------------
# Canonicalize entire matchup
# --------------------------------------------------

def canonicalize_matchup(
    matchup,
    league_hint=None
):

    parts = matchup_parts(
        matchup
    )


    if len(parts) != 2:

        return normalize_matchup(
            matchup
        )


    first = canonicalize_team_name(
        parts[0],
        league_hint
    )


    second = canonicalize_team_name(
        parts[1],
        league_hint
    )


    return (

        f"{first}"

        f" vs. "

        f"{second}"

    )


# --------------------------------------------------
# ESPN league configuration
# --------------------------------------------------

ESPN_LEAGUES = {

    "MLB": (

        "baseball",

        "mlb"

    ),

    "NBA": (

        "basketball",

        "nba"

    ),

    "NFL": (

        "football",

        "nfl"

    ),

    "NHL": (

        "hockey",

        "nhl"

    )

}


# --------------------------------------------------
# Parse ESPN event datetime
# --------------------------------------------------

def parse_espn_datetime(
    event
):

    event_date = event.get(
        "date"
    )


    if not event_date:

        debug_stats[
            "public_events_date_time_failed"
        ] += 1


        return None


    try:

        event_datetime = datetime.fromisoformat(

            event_date.replace(
                "Z",
                "+00:00"
            )

        )


    except ValueError:

        debug_stats[
            "public_events_date_time_failed"
        ] += 1


        return None


    return event_datetime.astimezone(

        ZoneInfo(
            "America/New_York"
        )

    )


# --------------------------------------------------
# ESPN scoreboard lookup
# --------------------------------------------------

def get_public_events(
    date_value,
    league_hint
):

    global public_api_lookups


    if league_hint not in ESPN_LEAGUES:

        return []


    sport, league = ESPN_LEAGUES[
        league_hint
    ]


    public_api_lookups += 1


    date_text = date_value.strftime(
        "%Y%m%d"
    )


    primary_url = (

        "https://site.api.espn.com/apis/site/v2/sports/"

        f"{sport}/"

        f"{league}/"

        "scoreboard"

    )


    fallback_url = (

        "https://cdn.espn.com/core/"

        f"{league}/"

        "scoreboard"

    )


    request_headers = {

        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),

        "Accept": "application/json, text/plain, */*",

        "Accept-Language": "en-US,en;q=0.9",

        "Referer": "https://www.espn.com/",

        "Origin": "https://www.espn.com",

    }


    def extract_events(data):

        if not isinstance(
            data,
            (dict, list)
        ):

            return []


        if isinstance(
            data,
            dict
        ):

            events = data.get(
                "events"
            )


            if isinstance(
                events,
                list
            ):

                usable_events = [

                    event

                    for event in events

                    if isinstance(
                        event,
                        dict
                    )

                    and (

                        event.get(
                            "competitions"
                        )

                        or

                        event.get(
                            "date"
                        )

                    )

                ]


                if usable_events:

                    return usable_events


            for value in data.values():

                found = extract_events(
                    value
                )


                if found:

                    return found


        else:

            for value in data:

                found = extract_events(
                    value
                )


                if found:

                    return found


        return []


    try:

        response = session.get(

            primary_url,

            params={

                "dates": date_text

            },

            headers=request_headers,

            timeout=30

        )


        if response.status_code == 200:

            data = response.json()


            events = extract_events(
                data
            )


            if events:

                debug_stats[
                    "public_events_downloaded"
                ] += len(events)


                return events


            print(
                "[ESPN PRIMARY] "
                "No events returned"
            )


        else:

            print(

                f"[ESPN PRIMARY] HTTP "

                f"{response.status_code}"

            )


        fallback_response = session.get(

            fallback_url,

            params={

                "xhr": "1",

                "limit": "100",

                "dates": date_text

            },

            headers=request_headers,

            timeout=30

        )


        if fallback_response.status_code != 200:

            debug_stats[
                "public_events_empty"
            ] += 1


            print(

                f"[ESPN FALLBACK] HTTP "

                f"{fallback_response.status_code}"

            )


            return []


        fallback_data = fallback_response.json()


        events = extract_events(
            fallback_data
        )


        if events:

            debug_stats[
                "public_events_downloaded"
            ] += len(events)


            print(

                f"[ESPN FALLBACK] "

                f"{len(events)} events returned"

            )


        else:

            debug_stats[
                "public_events_empty"
            ] += 1


            print(
                "[ESPN FALLBACK] "
                "No events returned"
            )


        return events


    except Exception as e:

        print(
            "[ESPN API ERROR]"
        )


        print(
            e
        )


        return []


# --------------------------------------------------
# Find ESPN event
#
# ESPN is the authoritative TIME source only.
#
# If the same matchup appears more than once on the same
# day, the provider start timestamp is first converted from
# the provider timezone to Eastern, then used only to select
# the correct ESPN event.
# --------------------------------------------------

def find_public_event(
    canonical_matchup,
    preferred_date,
    league_hint,
    provider_start_eastern=None,
    provider_time_clues=None,
    provider_game_number=None,
    return_doubleheader_if_unassigned=False
):

    global public_api_matches


    parts = matchup_parts(
        canonical_matchup
    )


    if len(parts) != 2:

        return None


    wanted_first = parts[0]

    wanted_second = parts[1]


    if provider_time_clues is None:

        provider_time_clues = []


    # Backward compatibility for callers that still pass only
    # the older converted provider start value.
    if (
        provider_start_eastern
        and not provider_time_clues
    ):

        provider_time_clues = [
            {
                "datetime": provider_start_eastern,
                "source": "converted provider start",
                "confidence": 100
            }
        ]


    print()

    print(
        "[ESPN LOOKUP]"
    )


    print(
        "Searching for:"
    )


    print(
        f"  {canonical_matchup}"
    )


    if provider_start_eastern:

        print(
            "Provider start converted to Eastern:"
        )


        print(
            f"  {provider_start_eastern}"
        )


    search_dates = [

        preferred_date,

        preferred_date - timedelta(
            days=1
        ),

        preferred_date + timedelta(
            days=1
        ),

        preferred_date + timedelta(
            days=2
        )

    ]


    # --------------------------------------------------
    # IMPORTANT:
    #
    # Do NOT return after the first ESPN date that has a
    # matching event. A doubleheader can otherwise collapse
    # to Game 1 if ESPN exposes the two games differently
    # across scoreboard date queries.
    #
    # Collect every matching ESPN event across all search
    # dates first, deduplicate them, and only then use the
    # converted provider start to identify which ESPN game
    # this provider channel represents.
    #
    # ESPN remains the authoritative FINAL time source.
    # --------------------------------------------------

    all_matching_events = []


    for date_value in search_dates:

        events = get_public_events(
            date_value,
            league_hint
        )


        if not events:

            print(
                f"[ESPN] {date_value}: "
                f"no events returned"
            )

            continue


        print(
            f"[ESPN] {date_value}: "
            f"{len(events)} events"
        )


        for event in events:

            competitions = event.get(
                "competitions",
                []
            )


            if not competitions:

                continue


            competition = competitions[0]


            competitors = competition.get(
                "competitors",
                []
            )


            if len(competitors) < 2:

                continue


            event_teams = []


            for competitor in competitors:

                team = competitor.get(
                    "team",
                    {}
                )


                display_name = clean_text(
                    team.get(
                        "displayName",
                        ""
                    )
                )


                short_name = clean_text(
                    team.get(
                        "shortDisplayName",
                        ""
                    )
                )


                location = clean_text(
                    team.get(
                        "location",
                        ""
                    )
                )


                nickname = clean_text(
                    team.get(
                        "name",
                        ""
                    )
                )


                event_teams.append(
                    {
                        "display_name": display_name,
                        "short_name": short_name,
                        "location": location,
                        "nickname": nickname
                    }
                )


            if len(event_teams) < 2:

                continue


            event_team_names = []


            for team in event_teams:

                candidates = [
                    team["display_name"],
                    team["short_name"],
                    team["location"],
                    team["nickname"]
                ]


                event_team_names.append(
                    [
                        canonicalize_team_name(
                            candidate,
                            league_hint
                        )
                        for candidate in candidates
                        if candidate
                    ]
                )


            wanted_first_match = any(
                team_matches(
                    wanted_first,
                    candidate
                )
                for candidate in event_team_names[0]
            ) or any(
                team_matches(
                    wanted_first,
                    candidate
                )
                for candidate in event_team_names[1]
            )


            wanted_second_match = any(
                team_matches(
                    wanted_second,
                    candidate
                )
                for candidate in event_team_names[0]
            ) or any(
                team_matches(
                    wanted_second,
                    candidate
                )
                for candidate in event_team_names[1]
            )


            if not (
                wanted_first_match
                and
                wanted_second_match
            ):

                debug_stats[
                    "public_events_team_match_failed"
                ] += 1

                continue


            event_datetime = parse_espn_datetime(
                event
            )


            if not event_datetime:

                continue


            event_id = clean_text(
                event.get(
                    "id",
                    ""
                )
            )


            all_matching_events.append(
                {
                    "datetime": event_datetime,
                    "event_teams": event_teams,
                    "event_id": event_id
                }
            )


    if not all_matching_events:

        print()

        print(
            "[ESPN MATCH FAIL]"
        )


        print(
            f"  No event/time found for: "
            f"{canonical_matchup}"
        )


        return None


    # --------------------------------------------------
    # DEDUPLICATE ESPN RESULTS
    #
    # The same event can be returned by more than one
    # scoreboard date query. Prefer ESPN event ID when
    # available; otherwise use its Eastern datetime.
    # --------------------------------------------------

    unique_events = {}

    for candidate in all_matching_events:

        event_id = candidate.get(
            "event_id"
        )


        if event_id:

            key = (
                "id",
                event_id
            )

        else:

            key = (
                "datetime",
                candidate["datetime"].isoformat()
            )


        if key not in unique_events:

            unique_events[
                key
            ] = candidate


    matching_events = list(
        unique_events.values()
    )


    matching_events.sort(
        key=lambda candidate: candidate[
            "datetime"
        ]
    )


    print()

    print(
        "[ESPN MATCHING EVENTS]"
    )


    for candidate in matching_events:

        print(
            f"  {candidate['datetime']}"
        )


    # --------------------------------------------------
    # SAME-TEAM MULTIPLE-GAME SAFETY CHECK
    #
    # Normal one-game matching is unchanged.
    #
    # If ESPN exposes more than one game for the same two
    # teams, NEVER silently default to the first event.
    #
    # Resolution order:
    #   1. Restrict to the relevant Eastern calendar date.
    #   2. If provider explicitly says Game 1 / Game 2 / G1 /
    #      G2, use that ordinal within ESPN's chronological
    #      games for that date.
    #   3. Otherwise compare every flexible provider time clue
    #      against the ESPN candidates and choose the closest.
    #   4. If multiple same-day games remain and there is no
    #      usable clue, fail safely instead of assigning Game 1
    #      to every channel.
    #
    # ESPN's selected datetime is always the FINAL guide time.
    # --------------------------------------------------

    events_by_eastern_date = {}


    for candidate in matching_events:

        event_date = candidate[
            "datetime"
        ].date()


        events_by_eastern_date.setdefault(
            event_date,
            []
        ).append(
            candidate
        )


    for date_candidates in events_by_eastern_date.values():

        date_candidates.sort(
            key=lambda candidate: candidate[
                "datetime"
            ]
        )


    target_events = events_by_eastern_date.get(
        preferred_date,
        []
    )


    # If the preferred date has no ESPN match, use provider
    # datetime clues to identify the nearest event/date.
    if not target_events and provider_time_clues:

        nearest_overall = min(
            (
                (
                    abs(
                        (
                            candidate["datetime"]
                            - clue["datetime"]
                        ).total_seconds()
                    ),
                    candidate
                )
                for candidate in matching_events
                for clue in provider_time_clues
            ),
            key=lambda item: item[0],
            default=None
        )


        if nearest_overall:

            nearest_date = nearest_overall[
                1
            ][
                "datetime"
            ].date()


            target_events = events_by_eastern_date.get(
                nearest_date,
                []
            )


    # --------------------------------------------------
    # SINGLE-PROVIDER-CHANNEL DOUBLEHEADER MODE
    #
    # The caller may explicitly request both games ONLY after
    # it has already verified that the provider exposes exactly
    # one channel for this canonical matchup/Eastern date.
    #
    # This never activates for a matchup such as Red Sox/Yankees
    # when two provider channels are present. In that case each
    # channel continues through the normal one-event selector.
    # --------------------------------------------------
    if (
        return_doubleheader_if_unassigned
        and len(target_events) == 2
    ):

        doubleheader_datetimes = [
            candidate["datetime"]
            for candidate in target_events
        ]

        doubleheader_datetimes.sort()

        print()
        print(
            "[SINGLE-CHANNEL DOUBLEHEADER DETECTED]"
        )
        print(
            f"  Matchup: {canonical_matchup}"
        )
        print(
            f"  Eastern date: {preferred_date}"
        )
        print(
            "  Provider matching channels: 1"
        )
        print(
            f"  ESPN Game 1: {doubleheader_datetimes[0]}"
        )
        print(
            f"  ESPN Game 2: {doubleheader_datetimes[1]}"
        )

        public_api_matches += 2
        debug_stats[
            "public_events_success"
        ] += 2

        return {
            "doubleheader_datetimes": doubleheader_datetimes
        }


    # If there is still no date-specific set, preserve normal
    # behavior only when ESPN found exactly one total event.
    if not target_events:

        if len(matching_events) == 1:

            selected = matching_events[0]


        else:

            print()

            print(
                "[ESPN MULTIPLE MATCHES AMBIGUOUS]"
            )


            print(
                "  Could not determine the relevant ESPN date."
            )


            print(
                "  Refusing to default to the first event."
            )


            return None


    elif len(target_events) == 1:

        # Normal one-game day. No matching behavior change.
        selected = target_events[0]


    else:

        print()

        print(
            "[ESPN SAME-TEAM MULTIPLE-GAME CHECK]"
        )


        print(
            f"  Eastern date: {target_events[0]['datetime'].date()}"
        )


        print(
            f"  Same-team games on date: {len(target_events)}"
        )


        for index, candidate in enumerate(
            target_events,
            start=1
        ):

            print(
                f"  ESPN game {index}: "
                f"{candidate['datetime']}"
            )


        selected = None


        # Strongest human clue: explicit Game 1 / Game 2 / G1 / G2.
        if (
            provider_game_number
            and
            1 <= provider_game_number <= len(target_events)
        ):

            selected = target_events[
                provider_game_number - 1
            ]


            print(
                f"  Provider game number: "
                f"{provider_game_number}"
            )


            print(
                "  Resolution: explicit game ordinal"
            )


        # Otherwise use every plausible timezone interpretation
        # produced by the flexible provider-time parser.
        if selected is None and provider_time_clues:

            scored_candidates = []


            for candidate in target_events:

                best_for_candidate = None


                for clue in provider_time_clues:

                    difference_seconds = abs(
                        (
                            candidate["datetime"]
                            - clue["datetime"]
                        ).total_seconds()
                    )


                    score = (
                        difference_seconds,
                        -int(
                            clue.get(
                                "confidence",
                                0
                            )
                        )
                    )


                    if (
                        best_for_candidate is None
                        or score < best_for_candidate["score"]
                    ):

                        best_for_candidate = {
                            "score": score,
                            "difference_seconds": difference_seconds,
                            "clue": clue
                        }


                if best_for_candidate:

                    scored_candidates.append(
                        {
                            "candidate": candidate,
                            "difference_seconds": best_for_candidate[
                                "difference_seconds"
                            ],
                            "clue": best_for_candidate[
                                "clue"
                            ]
                        }
                    )


            scored_candidates.sort(
                key=lambda item: (
                    item["difference_seconds"],
                    -int(
                        item["clue"].get(
                            "confidence",
                            0
                        )
                    ),
                    item["candidate"]["datetime"]
                )
            )


            if scored_candidates:

                best = scored_candidates[0]


                # Detect a true tie. If two ESPN games are equally
                # supported by the available provider clues, do not
                # guess which one it is.
                tied = [
                    item
                    for item in scored_candidates
                    if abs(
                        item["difference_seconds"]
                        - best["difference_seconds"]
                    ) < 1
                ]


                if len(tied) == 1:

                    selected = best[
                        "candidate"
                    ]


                    print(
                        "  Resolution: closest provider-time clue"
                    )


                    print(
                        f"  Winning clue: "
                        f"{best['clue']['source']}"
                    )


                    print(
                        f"  Winning clue Eastern: "
                        f"{best['clue']['datetime']}"
                    )


                    print(
                        f"  Difference: "
                        f"{best['difference_seconds'] / 60:.1f} minutes"
                    )


                else:

                    print(
                        "  Provider clues produced an exact tie."
                    )


        if selected is None:

            print()

            print(
                "[ESPN MULTIPLE MATCHES AMBIGUOUS]"
            )


            print(
                f"  Matchup: {canonical_matchup}"
            )


            print(
                "  ESPN has multiple same-day games."
            )


            print(
                "  No provider clue safely identifies which one."
            )


            print(
                "  Refusing to reuse the first ESPN game."
            )


            return None


    if provider_time_clues:

        print()

        print(
            "[PROVIDER TIME CLUES]"
        )


        for clue in provider_time_clues:

            print(
                f"  {clue['datetime']} | "
                f"{clue['source']} | "
                f"confidence={clue.get('confidence', 0)}"
            )


    print()

    print(
        "[ESPN EVENT SELECTED]"
    )


    print(
        f"  {selected['datetime']}"
    )


    event_datetime = selected[
        "datetime"
    ]


    event_teams = selected[
        "event_teams"
    ]


    public_api_matches += 1


    debug_stats[
        "public_events_success"
    ] += 1


    print()

    print(
        "[ESPN MATCH SUCCESS]"
    )


    print(
        f"  Clean matchup: "
        f"{canonical_matchup}"
    )


    print(
        "  ESPN event teams:"
    )


    for team in event_teams:

        print(
            f"    {team['display_name']}"
        )


    print(
        f"  Verified Eastern time: "
        f"{event_datetime}"
    )


    # ESPN supplies only the verified datetime.
    return {
        "datetime": event_datetime
    }


# --------------------------------------------------
# Team matching
# --------------------------------------------------

def team_matches(
    wanted_team,
    actual_team
):

    wanted_team = normalize_team_name(

        wanted_team

    )


    actual_team = normalize_team_name(

        actual_team

    )


    if not wanted_team or not actual_team:

        return False


    if wanted_team == actual_team:

        return True


    if (

        wanted_team in actual_team

        or

        actual_team in wanted_team

    ):

        return True


    wanted_words = set(

        wanted_team.split()

    )


    actual_words = set(

        actual_team.split()

    )


    if wanted_words.issubset(

        actual_words

    ):

        return True


    meaningful_words = {

        word

        for word in wanted_words

        if len(word) >= 4

    }


    shared_words = (

        wanted_words

        &

        actual_words

    )


    return bool(

        meaningful_words

        and

        meaningful_words.issubset(

            shared_words

        )

    )


# --------------------------------------------------
# Create safe logo filename
# --------------------------------------------------

def clean_logo_filename(text):

    text = clean_text(
        text
    )


    text = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        text
    )


    text = re.sub(
        r"_+",
        "_",
        text
    )


    return text.strip(
        "_"
    )


# --------------------------------------------------
# Logo-name compatibility aliases.
# --------------------------------------------------

def normalize_logo_team_name(
    team_name,
    league_hint=None
):

    normalized = normalize_team_name(
        team_name
    )


    if league_hint == "NBA" and normalized in {

        "new york knicks",

        "ny knicks",

        "knicks"

    }:

        return "new york knicks"


    if league_hint == "NHL" and normalized in {

        "arizona coyotes",

        "phoenix coyotes",

        "coyotes",

        "utah hockey club",

        "utah hc",

        "utah mammoth",

        "mammoth"

    }:

        return "utah mammoth"


    return normalized


# --------------------------------------------------
# Create normalized logo key
# --------------------------------------------------

def matchup_logo_key(
    first_team,
    second_team,
    league_hint=None
):

    first = normalize_logo_team_name(
        first_team,
        league_hint
    )


    second = normalize_logo_team_name(
        second_team,
        league_hint
    )


    if not first or not second:

        return None


    return (

        first,

        second

    )


# --------------------------------------------------
# Find matchup logo.
# --------------------------------------------------

def find_matchup_logo(
    canonical_matchup,
    league_hint
):

    global logos_found

    global logos_missing


    parts = matchup_parts(

        canonical_matchup

    )


    if len(parts) != 2:

        logos_missing += 1


        debug_stats[

            "logo_not_found"

        ] += 1


        return None


    first_team = canonicalize_team_name(

        parts[0],

        league_hint

    )


    second_team = canonicalize_team_name(

        parts[1],

        league_hint

    )


    wanted_first = normalize_logo_team_name(

        first_team,

        league_hint

    )


    wanted_second = normalize_logo_team_name(

        second_team,

        league_hint

    )


    if not wanted_first or not wanted_second:

        logos_missing += 1


        debug_stats[

            "logo_not_found"

        ] += 1


        return None


    print()

    print(
        "[LOGO SEARCH]"
    )


    print(
        f"  League: "
        f"{league_hint}"
    )


    print(
        f"  Ordered teams: "
        f"{first_team} vs. {second_team}"
    )


    league_root = os.path.join(

        SPORTS_LOGO_ROOT,

        league_hint

    )


    if not os.path.isdir(

        league_root

    ):

        logos_missing += 1


        debug_stats[

            "logo_not_found"

        ] += 1


        print(
            "  League logo directory does not exist."
        )


        return None


    first_filename = clean_logo_filename(

        first_team

    )


    second_filename = clean_logo_filename(

        second_team

    )


    exact_path = os.path.join(

        league_root,

        first_filename,

        (

            f"{first_filename}"

            f"_vs_"

            f"{second_filename}"

            f".png"

        )

    )


    candidate_path = None


    if os.path.isfile(

        exact_path

    ):

        candidate_path = exact_path


    else:

        for root, directories, files in os.walk(

            league_root

        ):

            for filename in files:

                if not filename.lower().endswith(

                    ".png"

                ):

                    continue


                file_stem = os.path.splitext(

                    filename

                )[0]


                if "_vs_" not in file_stem:

                    continue


                file_parts = file_stem.split(

                    "_vs_",

                    1

                )


                if len(file_parts) != 2:

                    continue


                logo_first = canonicalize_team_name(

                    file_parts[0].replace(

                        "_",

                        " "

                    ),

                    league_hint

                )


                logo_second = canonicalize_team_name(

                    file_parts[1].replace(

                        "_",

                        " "

                    ),

                    league_hint

                )


                logo_first_ordered = normalize_logo_team_name(

                    logo_first,

                    league_hint

                )


                logo_second_ordered = normalize_logo_team_name(

                    logo_second,

                    league_hint

                )


                if (

                    logo_first_ordered != wanted_first

                    or

                    logo_second_ordered != wanted_second

                ):

                    continue


                candidate_path = os.path.join(

                    root,

                    filename

                )


                break


            if candidate_path:

                break


    if not candidate_path:

        logos_missing += 1


        debug_stats[

            "logo_not_found"

        ] += 1


        print(
            "  No correctly ordered matching logo found."
        )


        return None


    relative_path = os.path.relpath(

        candidate_path,

        "."

    )


    relative_path = relative_path.replace(

        os.sep,

        "/"

    )


    encoded_path = "/".join(

        quote(

            part,

            safe=""

        )

        for part in relative_path.split(

            "/"

        )

    )


    logo_url = (

        GITHUB_RAW_ROOT

        + encoded_path

    )


    logos_found += 1


    debug_stats[

        "logo_direct_order_found"

    ] += 1


    print(
        "  Ordered logo found:"
    )


    print(
        f"  {relative_path}"
    )


    print(
        f"  {logo_url}"
    )


    return logo_url


# --------------------------------------------------
# Rename legacy provider team identities before any
# display-name, title, description, or logo processing.
# --------------------------------------------------

def rename_legacy_team_identity(text):

    if not text:

        return text


    return re.sub(
        r"\b(?:"
        r"ARIZONA\s+COYOTES|"
        r"PHOENIX\s+COYOTES|"
        r"UTAH\s+HOCKEY\s+CLUB|"
        r"UTAH\s+HC"
        r")\b",
        "UTAH MAMMOTH",
        str(text),
        flags=re.IGNORECASE
    )


# --------------------------------------------------
# Detect whether the current channel/event name is
# simply one known MLB/NBA/NFL/NHL team.
# --------------------------------------------------

def detect_single_team(
    provider_name,
    league_hint=None
):

    if not provider_name:

        return None


    raw_name = clean_text(
        rename_legacy_team_identity(
            provider_name
        )
    )


    if matchup_parts(
        normalize_matchup(
            re.sub(
                r"\s*\([A-Za-z0-9]{2,5}\)\s*",
                " ",
                raw_name
            )
        )
    ):

        return None


    if re.search(
        r"\b(?:"
        r"NO\s+EVENT(?:\s+STREAMING)?|"
        r"NO\s+INFORMATION|"
        r"OFF\s*AIR|"
        r"EVENT\s+NOT\s+STARTED|"
        r"COMING\s+SOON|"
        r"TO\s+BE\s+ANNOUNCED|"
        r"TBA|"
        r"NBA\s+PASS\s+PPV|"
        r"LEAGUE\s+PASS\s+PPV"
        r")\b",
        raw_name,
        flags=re.IGNORECASE
    ):

        return None


    candidate = raw_name


    candidate = re.sub(
        r"^(?:US|CA|UK)\s*:\s*",
        "",
        candidate,
        flags=re.IGNORECASE
    )


    candidate = re.sub(
        r"^(?:8K\s+EXCLUSIVE\s*[|:-]\s*)+",
        "",
        candidate,
        flags=re.IGNORECASE
    )


    candidate = re.sub(
        r"^(?:MLB|NBA|NFL|NHL)"
        r"(?:\s+(?:CHANNEL\s*)?\d+)?"
        r"\s*[-:|]?\s*",
        "",
        candidate,
        flags=re.IGNORECASE
    )


    candidate = re.sub(
        r"\s*\([A-Za-z0-9]{2,5}\)\s*",
        " ",
        candidate
    )


    candidate = re.sub(
        r"\b(?:RAW|HD|FHD|UHD|SD|4K|8K|FEED)\b",
        " ",
        candidate,
        flags=re.IGNORECASE
    )


    candidate = re.sub(
        r"[ᴿᴬᵂᴴᴰ⁴ᴷ⁸ᴷ]+",
        " ",
        candidate
    )


    candidate = re.sub(
        r"\s*[-|:]\s*$",
        "",
        candidate
    )


    candidate = clean_text(
        candidate
    )


    candidate_normalized = normalize_team_name(
        candidate
    )


    if not candidate_normalized:

        return None


    leagues = (

        [league_hint]

        if league_hint in team_aliases

        else list(
            SUPPORTED_MAJOR_LEAGUES
        )

    )


    for league in leagues:

        if league == "NHL" and candidate_normalized == "utah mammoth":

            return (
                "NHL",
                "Utah Mammoth"
            )


        official_name = team_aliases[
            league
        ].get(
            candidate_normalized
        )


        if official_name:

            return (
                league,
                official_name
            )


    return None


# --------------------------------------------------
# Find an existing single-team logo.
# --------------------------------------------------

def find_single_team_logo(
    official_team,
    league_hint
):

    global logos_found

    global logos_missing


    wanted_team = normalize_logo_team_name(
        official_team,
        league_hint
    )


    if not wanted_team:

        return None


    league_root = os.path.join(
        SPORTS_LOGO_ROOT,
        league_hint
    )


    if not os.path.isdir(
        league_root
    ):

        debug_stats[
            "single_team_logo_missing"
        ] += 1


        logos_missing += 1


        return None


    for root, directories, files in os.walk(
        league_root
    ):

        for filename in files:

            if not filename.lower().endswith(
                ".png"
            ):

                continue


            file_stem = os.path.splitext(
                filename
            )[0]


            if "_vs_" in file_stem.lower():

                continue


            file_team = normalize_logo_team_name(
                file_stem.replace(
                    "_",
                    " "
                ),
                league_hint
            )


            file_official = team_aliases[
                league_hint
            ].get(
                file_team
            )


            if file_official:

                file_team = normalize_logo_team_name(
                    file_official,
                    league_hint
                )


            official_alias = team_aliases[
                league_hint
            ].get(
                wanted_team
            )


            if official_alias:

                wanted_compare = normalize_logo_team_name(
                    official_alias,
                    league_hint
                )

            else:

                wanted_compare = wanted_team


            if file_team != wanted_compare:

                continue


            relative_path = os.path.relpath(
                os.path.join(
                    root,
                    filename
                ),
                "."
            )


            relative_path = relative_path.replace(
                os.sep,
                "/"
            )


            encoded_path = "/".join(
                quote(
                    part,
                    safe=""
                )
                for part in relative_path.split(
                    "/"
                )
            )


            logo_url = (
                GITHUB_RAW_ROOT
                + encoded_path
            )


            logos_found += 1


            debug_stats[
                "single_team_logo_found"
            ] += 1


            print()


            print(
                "[SINGLE TEAM LOGO FOUND]"
            )


            print(
                f"  Team: {official_team}"
            )


            print(
                f"  {relative_path}"
            )


            return logo_url


    debug_stats[
        "single_team_logo_missing"
    ] += 1


    logos_missing += 1


    return None


# --------------------------------------------------
# Build event information
#
# Order:
#
# 1. Read provider matchup
# 2. Clean team names using sports_teams.txt
# 3. Use provider date only as an ESPN search hint
# 4. ESPN verifies the matching game's date/time only
# 5. Build title/description and select our ordered logo
#
# The verified ESPN datetime is returned to the caller so
# the scheduler can use it without making another ESPN API
# request.
# --------------------------------------------------

def build_event_info(
    stream,
    provider_matchup_channel_count=1
):

    global verified_public_times_used

    global no_public_match


    provider_name = clean_text(

        rename_legacy_team_identity(

            stream.get(

                "name",

                ""

            )

        )

    )


    stream_id = str(

        stream.get(

            "stream_id",

            ""

        )

    )


    # --------------------------------------------------
    # NFL RedZone display cleanup.
    # --------------------------------------------------

    if stream_id == "1031379":

        provider_name = "NFL RED ZONE"


    print()

    print(
        "=================================================="
    )


    print(

        f"[CHANNEL {stream_id}]"

    )


    print(
        "Raw provider name:"
    )


    print(
        f"  {provider_name}"
    )


    # --------------------------------------------------
    # STEP 1:
    # Extract provider matchup.
    # --------------------------------------------------

    provider_event = extract_provider_matchup(

        provider_name

    )


    provider_fallback_event = extract_provider_event_text(

        provider_name

    )


    print(
        "Extracted matchup:"
    )


    print(
        f"  {provider_event}"
    )


    # --------------------------------------------------
    # STEP 2:
    # Detect league.
    # --------------------------------------------------

    league_hint = detect_league(

        provider_name

    )


    print(
        "Detected league:"
    )


    print(
        f"  {league_hint}"
    )


    single_team = detect_single_team(

        provider_name,

        league_hint

    )


    # --------------------------------------------------
    # STEP 3:
    # Clean team names using sports_teams.txt.
    # --------------------------------------------------

    canonical_matchup = canonicalize_matchup(

        provider_event,

        league_hint

    )


    print(
        "Clean canonical matchup:"
    )


    print(
        f"  {canonical_matchup}"
    )


    # --------------------------------------------------
    # STEP 4:
    # Determine preferred date.
    # --------------------------------------------------

    provider_start = extract_start_datetime(

        provider_name

    )


    provider_start_eastern = None


    if provider_start:

        provider_start_eastern = (

            convert_to_eastern(

                provider_start,

                provider_timezone

            )

        )


    provider_time_clues = extract_provider_time_clues(

        provider_name,

        provider_timezone

    )


    provider_game_number = extract_provider_game_number(

        provider_name

    )


    provider_date_hint = extract_provider_date_hint(

        provider_name

    )


    preferred_date = (

        provider_start_eastern.date()

        if provider_start_eastern

        else (

            provider_date_hint

            if provider_date_hint

            else datetime.now(

                ZoneInfo(

                    "America/New_York"

                )

            ).date()

        )

    )


    # --------------------------------------------------
    # STEP 5:
    # Build clean fallback title/description.
    # --------------------------------------------------

    if canonical_matchup:

        title_text = canonical_matchup


        description_text = (

            f"{canonical_matchup}\n"

            f"{preferred_date.strftime('%A')} "

            f"{preferred_date.strftime('%m/%d/%Y')}"

        )


    else:

        fallback_title = (

            single_team[1]

            if single_team

            else (

                provider_fallback_event

                or provider_name

                or "Sports Event"

            )

        )


        if fallback_title != "Sports Event":

            debug_stats[
                "provider_name_fallback_used"
            ] += 1


        title_text = fallback_title


        description_text = (

            f"{fallback_title}\n"

            f"{preferred_date.strftime('%A')} "

            f"{preferred_date.strftime('%m/%d/%Y')}"

        )


    # --------------------------------------------------
    # STEP 6:
    # ESPN verifies the matching game's date/time only.
    # --------------------------------------------------

    public_event = None


    if canonical_matchup and league_hint:

        public_event = find_public_event(

            canonical_matchup,

            preferred_date,

            league_hint,

            provider_start_eastern,

            provider_time_clues,

            provider_game_number,

            return_doubleheader_if_unassigned=(
                provider_matchup_channel_count == 1
            )

        )


    doubleheader_datetimes = None

    if (
        public_event
        and public_event.get(
            "doubleheader_datetimes"
        )
    ):

        doubleheader_datetimes = list(
            public_event[
                "doubleheader_datetimes"
            ]
        )

        # There is deliberately no single selected ESPN event
        # in this mode. Both verified times belong to this one
        # provider channel.
        public_event = None


    # --------------------------------------------------
    # STEP 7:
    # Find the matchup logo using OUR cleaned matchup.
    # --------------------------------------------------

    logo_url = None


    if canonical_matchup and league_hint:

        logo_url = find_matchup_logo(

            canonical_matchup,

            league_hint

        )


        # --------------------------------------------------
        # LOGO-FAILURE TYPO RECOVERY.
        # --------------------------------------------------

        if not logo_url:

            recovered = recover_logo_typo_matchup(

                canonical_matchup,

                league_hint,

                preferred_date

            )


            if recovered:

                corrected_matchup, corrected_event = recovered

                canonical_matchup = corrected_matchup

                public_event = corrected_event


                global logos_missing

                if logos_missing > 0:

                    logos_missing -= 1


                if debug_stats["logo_not_found"] > 0:

                    debug_stats["logo_not_found"] -= 1


                logo_url = find_matchup_logo(

                    canonical_matchup,

                    league_hint

                )


    elif single_team:

        single_team_league = single_team[

            0

        ]


        single_team_name = single_team[

            1

        ]


        logo_url = find_single_team_logo(

            single_team_name,

            single_team_league

        )


    # --------------------------------------------------
    # NFL RedZone fixed-logo override.
    # --------------------------------------------------

    if stream_id == "1031379":

        logo_url = REDZONE_LOGO_URL


        print()

        print(
            "[NFL REDZONE LOGO]"
        )


        print(
            f"  {logo_url}"
        )


    # --------------------------------------------------
    # STEP 8:
    # If ESPN found the game, add verified
    # Eastern date/time to BOTH title and description.
    # --------------------------------------------------

    verified_game_datetime = None


    if doubleheader_datetimes:

        verified_public_times_used += len(
            doubleheader_datetimes
        )

        print()
        print(
            "[FINAL DOUBLEHEADER EVENT DATA]"
        )
        print(
            f"  Matchup: {canonical_matchup}"
        )
        print(
            f"  Game 1: {doubleheader_datetimes[0]}"
        )
        print(
            f"  Game 2: {doubleheader_datetimes[1]}"
        )
        print(
            f"  Logo: {logo_url}"
        )

        return (
            canonical_matchup,
            description_text,
            logo_url,
            True,
            None,
            doubleheader_datetimes
        )


    if public_event:

        verified_public_times_used += 1


        event_datetime = public_event[

            "datetime"

        ]


        verified_game_datetime = event_datetime


        event_time_text = (

            event_datetime.strftime(

                "%-I:%M %p"

            )

        )


        title_text = (

            f"{canonical_matchup}"

            f" - "

            f"{event_time_text}"

        )


        description_text = (

            f"{canonical_matchup}\n"

            f"{event_datetime.strftime('%A')} "

            f"{event_datetime.strftime('%m/%d/%Y')}"

            f" - "

            f"{event_time_text}"

        )


        print()

        print(
            "[FINAL EVENT DATA]"
        )


        print(
            f"  Title: "
            f"{title_text}"
        )


        print(
            f"  Description: "
            f"{description_text}"
        )


        print(
            f"  Logo: "
            f"{logo_url}"
        )


        print(
            f"  Scheduling game start: "
            f"{verified_game_datetime}"
        )


        return (

            title_text,

            description_text,

            logo_url,

            True,

            verified_game_datetime,

            None

        )


    # --------------------------------------------------
    # ESPN failed.
    #
    # Keep clean title and description.
    #
    # Logo remains valid if found.
    # --------------------------------------------------

    no_public_match += 1


    print()

    print(
        "[FINAL EVENT DATA]"
    )


    print(
        f"  Title: "
        f"{title_text}"
    )


    print(
        f"  Description: "
        f"{description_text}"
    )


    print(
        f"  Logo: "
        f"{logo_url}"
    )


    return (

        title_text,

        description_text,

        logo_url,

        False,

        None,

        None

    )


# --------------------------------------------------
# Create XMLTV root
# --------------------------------------------------

tv = ET.Element(

    "tv",

    {

        "generator-info-name":

        "Festy Sports Guide"

    }

)


# --------------------------------------------------
# Calculate guide period
# --------------------------------------------------

guide_start = (

    datetime.now(

        timezone.utc

    ).astimezone(

        ZoneInfo(

            "America/New_York"

        )

    ).replace(

        hour=0,

        minute=0,

        second=0,

        microsecond=0

    )

)


guide_end = (

    guide_start

    + timedelta(

        days=3

    )

)


# --------------------------------------------------
# Create XMLTV channels
# --------------------------------------------------

print()

print(
    "Creating XMLTV channels..."
)


matched = 0

channel_elements = {}


for channel_id, requested_name in wanted.items():

    if channel_id not in provider:

        continue


    matched += 1


    stream = provider[

        channel_id

    ]


    provider_name = rename_legacy_team_identity(

        stream.get(

            "name",

            requested_name

        )

    )


    channel = ET.SubElement(

        tv,

        "channel",

        {

            "id":

            channel_id

        }

    )


    display = ET.SubElement(

        channel,

        "display-name"

    )


    display.text = provider_name


    channel_elements[

        channel_id

    ] = channel


# --------------------------------------------------
# Create 3-hour programme blocks.
#
# Normal blocks are anchored to:
#
# 12 AM - 3 AM
# 3 AM  - 6 AM
# 6 AM  - 9 AM
# 9 AM  - 12 PM
# 12 PM - 3 PM
# 3 PM  - 6 PM
# 6 PM  - 9 PM
# 9 PM  - 12 AM
#
# If ESPN provides a verified game start inside one of
# those blocks, the schedule is split around the game:
#
# normal block start -> game start
# game start -> game start + 3 hours
# game end -> next normal 3-hour boundary
#
# The actual game remains exactly 3 hours.
#
# Everything BEFORE the game is Upcoming.
# The actual game keeps the normal title.
# Everything AFTER the game is Post Game.
# --------------------------------------------------

print()

print(
    "Creating 3-hour programme blocks with ESPN game scheduling..."
)


# --------------------------------------------------
# Count provider channels per canonical major-league matchup/date.
#
# This is the gate for the special one-channel doubleheader mode.
# Two provider channels for the same matchup/date means each channel
# keeps normal independent event selection. Only exactly ONE provider
# channel allows two same-day ESPN games to share that channel.
# --------------------------------------------------
major_matchup_channel_counts = {}
major_matchup_key_by_channel = {}


def provider_major_matchup_key(stream):

    provider_name = clean_text(
        rename_legacy_team_identity(
            stream.get(
                "name",
                ""
            )
        )
    )

    league_hint = detect_league(
        provider_name
    )

    if league_hint not in SUPPORTED_MAJOR_LEAGUES:
        return None

    provider_event = extract_provider_matchup(
        provider_name
    )

    canonical_matchup = canonicalize_matchup(
        provider_event,
        league_hint
    )

    parts = matchup_parts(
        canonical_matchup
    )

    if len(parts) != 2:
        return None

    provider_start = extract_start_datetime(
        provider_name
    )

    provider_start_eastern = (
        convert_to_eastern(
            provider_start,
            provider_timezone
        )
        if provider_start
        else None
    )

    provider_date_hint = extract_provider_date_hint(
        provider_name
    )

    eastern_date = (
        provider_start_eastern.date()
        if provider_start_eastern
        else (
            provider_date_hint
            if provider_date_hint
            else datetime.now(
                ZoneInfo(
                    "America/New_York"
                )
            ).date()
        )
    )

    # Team order is intentionally ignored for counting so a provider
    # reversing home/away text still counts as the same matchup.
    team_key = tuple(
        sorted(
            normalize_team_name(
                team
            )
            for team in parts
        )
    )

    return (
        league_hint,
        team_key,
        eastern_date
    )


for channel_id, requested_name in wanted.items():

    if channel_id not in provider:
        continue

    matchup_key = provider_major_matchup_key(
        provider[
            channel_id
        ]
    )

    if not matchup_key:
        continue

    major_matchup_key_by_channel[
        channel_id
    ] = matchup_key

    major_matchup_channel_counts[
        matchup_key
    ] = (
        major_matchup_channel_counts.get(
            matchup_key,
            0
        )
        + 1
    )


for channel_id, requested_name in wanted.items():

    if channel_id not in provider:

        continue


    stream = provider[

        channel_id

    ]


    print()

    print(

        f"Processing {channel_id}"

    )


    (

        title_text,

        description_text,

        logo_url,

        has_real_epg,

        verified_game_datetime,

        doubleheader_datetimes

    ) = build_event_info(

        stream,

        provider_matchup_channel_count=(
            major_matchup_channel_counts.get(
                major_matchup_key_by_channel.get(
                    channel_id
                ),
                1
            )
        )

    )


    # --------------------------------------------------
    # Add matchup or single-team logo to CHANNEL.
    #
    # Independent of ESPN success/failure.
    # --------------------------------------------------

    if logo_url:

        channel = channel_elements[

            channel_id

        ]


        icon = ET.SubElement(

            channel,

            "icon"

        )


        icon.set(

            "src",

            logo_url

        )


    # --------------------------------------------------
    # SPECIAL: ONE-CHANNEL DOUBLEHEADER
    #
    # This branch exists only when ESPN found exactly two games
    # for the same canonical teams/Eastern date AND the provider
    # pre-scan found exactly one matching channel.
    #
    # Sequence:
    #   Upcoming Game 1 -> Game 1 (3h) -> Post Game 1 (1h)
    #   -> Upcoming Game 2 -> Game 2 (3h) -> normal Post Game 2
    #
    # After Game 2, normal boundary-aligned Post Game behavior
    # continues exactly like an ordinary verified game.
    # --------------------------------------------------
    if doubleheader_datetimes:

        game1_start, game2_start = sorted(
            doubleheader_datetimes
        )

        game1_end = (
            game1_start
            + timedelta(
                hours=3
            )
        )

        game2_end = (
            game2_start
            + timedelta(
                hours=3
            )
        )

        game1_time_text = game1_start.strftime(
            "%-I:%M %p"
        )

        game2_time_text = game2_start.strftime(
            "%-I:%M %p"
        )

        game1_title = (
            f"{title_text} - "
            f"Game 1 of Doubleheader - "
            f"{game1_time_text}"
        )

        game2_title = (
            f"{title_text} - "
            f"Game 2 of Doubleheader - "
            f"{game2_time_text}"
        )

        game1_description = (
            f"{title_text}\n"
            f"Game 1 of Doubleheader\n"
            f"{game1_start.strftime('%A')} "
            f"{game1_start.strftime('%m/%d/%Y')}"
            f" - {game1_time_text}"
        )

        game2_description = (
            f"{title_text}\n"
            f"Game 2 of Doubleheader\n"
            f"{game2_start.strftime('%A')} "
            f"{game2_start.strftime('%m/%d/%Y')}"
            f" - {game2_time_text}"
        )

        upcoming_game1_title = (
            f"Upcoming: {game1_title}"
        )

        post_game1_title = (
            f"Post Game: {game1_title}"
        )

        upcoming_game2_title = (
            f"Upcoming: {game2_title}"
        )

        post_game2_title = (
            f"Post Game: {game2_title}"
        )

        print()
        print(
            "[DOUBLEHEADER BLOCK SCHEDULING]"
        )
        print(
            f"  Game 1: {game1_start} -> {game1_end}"
        )
        print(
            f"  Game 2: {game2_start} -> {game2_end}"
        )
        print(
            "  Game 1 Post Game forced for first hour after Game 1"
        )
        print(
            "  Game 2 Post Game resumes normal blocking after Game 2"
        )

        def emit_doubleheader_programme(
            start_value,
            stop_value,
            programme_title,
            programme_description
        ):

            clipped_start = max(
                start_value,
                guide_start
            )

            clipped_stop = min(
                stop_value,
                guide_end
            )

            if clipped_start >= clipped_stop:
                return

            programme = ET.SubElement(
                tv,
                "programme",
                {
                    "start": clipped_start.strftime(
                        "%Y%m%d%H%M%S %z"
                    ),
                    "stop": clipped_stop.strftime(
                        "%Y%m%d%H%M%S %z"
                    ),
                    "channel": channel_id
                }
            )

            title = ET.SubElement(
                programme,
                "title"
            )

            title.text = programme_title

            desc = ET.SubElement(
                programme,
                "desc"
            )

            desc.text = programme_description

        # Upcoming Game 1 keeps the normal three-hour boundary
        # structure until the exact ESPN Game 1 start.
        current_start = guide_start

        while current_start < min(
            game1_start,
            guide_end
        ):

            elapsed_seconds = (
                current_start
                - guide_start
            ).total_seconds()

            completed_blocks = int(
                elapsed_seconds
                // (3 * 60 * 60)
            )

            next_boundary = (
                guide_start
                + timedelta(
                    hours=(
                        completed_blocks + 1
                    ) * 3
                )
            )

            current_stop = min(
                next_boundary,
                game1_start,
                guide_end
            )

            emit_doubleheader_programme(
                current_start,
                current_stop,
                upcoming_game1_title,
                game1_description
            )

            current_start = current_stop

        # Exact three-hour Game 1 block.
        emit_doubleheader_programme(
            game1_start,
            game1_end,
            game1_title,
            game1_description
        )

        # The FIRST hour after Game 1 is always Post Game, unless
        # Game 2 itself starts sooner (never overlap programmes).
        game1_post_end = min(
            game1_end + timedelta(
                hours=1
            ),
            game2_start
        )

        emit_doubleheader_programme(
            game1_end,
            game1_post_end,
            post_game1_title,
            game1_description
        )

        # After that one Post Game hour, switch to Upcoming Game 2
        # and hold it until ESPN's exact Game 2 start.
        emit_doubleheader_programme(
            game1_post_end,
            game2_start,
            upcoming_game2_title,
            game2_description
        )

        # Exact three-hour Game 2 block.
        emit_doubleheader_programme(
            game2_start,
            game2_end,
            game2_title,
            game2_description
        )

        # After Game 2, resume the same normal Post Game blocking
        # used by an ordinary game: first to the next anchored
        # three-hour boundary, then full three-hour blocks.
        current_start = max(
            game2_end,
            guide_start
        )

        while current_start < guide_end:

            elapsed_seconds = (
                current_start
                - guide_start
            ).total_seconds()

            completed_blocks = int(
                elapsed_seconds
                // (3 * 60 * 60)
            )

            next_boundary = (
                guide_start
                + timedelta(
                    hours=(
                        completed_blocks + 1
                    ) * 3
                )
            )

            if next_boundary <= current_start:
                next_boundary = (
                    current_start
                    + timedelta(
                        hours=3
                    )
                )

            current_stop = min(
                next_boundary,
                guide_end
            )

            emit_doubleheader_programme(
                current_start,
                current_stop,
                post_game2_title,
                game2_description
            )

            current_start = current_stop

        continue


    # --------------------------------------------------
    # ESPN game scheduling information.
    #
    # Every verified game is assumed to last exactly
    # three hours.
    #
    # No second ESPN lookup is performed here.
    # The datetime returned by build_event_info() is
    # the same verified ESPN datetime already used for
    # the title and description.
    # --------------------------------------------------

    game_start = verified_game_datetime


    game_end = (

        game_start

        + timedelta(

            hours=3

        )

        if game_start

        else None

    )


    # --------------------------------------------------
    # UPCOMING / POST-GAME TITLES
    #
    # The actual game keeps the normal title.
    #
    # Before the game:
    #     Upcoming: Team A vs. Team B - 7:00 PM
    #
    # During the game:
    #     Team A vs. Team B - 7:00 PM
    #
    # After the game:
    #     Post Game: Team A vs. Team B - 7:00 PM
    #
    # Descriptions remain unchanged.
    # --------------------------------------------------

    upcoming_title_text = (

        f"Upcoming: {title_text}"

    )


    post_game_title_text = (

        f"Post Game: {title_text}"

    )


    # --------------------------------------------------
    # ONE scheduling-state flag.
    #
    # This is the fix:
    #
    # - False = game has not yet occurred
    # - True  = game has finished
    #
    # It also correctly handles a verified game that
    # started before guide_start.
    # --------------------------------------------------

    game_has_occurred = (

        bool(game_end)

        and

        game_end <= guide_start

    )


    if game_start:

        print()

        print(
            "[GAME BLOCK SCHEDULING]"
        )


        print(
            f"  ESPN verified start: "
            f"{game_start}"
        )


        print(
            f"  Assumed game end: "
            f"{game_end}"
        )


        print(
            f"  Upcoming title: "
            f"{upcoming_title_text}"
        )


        print(
            f"  Game title: "
            f"{title_text}"
        )


        print(
            f"  Post-game title: "
            f"{post_game_title_text}"
        )


    current_start = guide_start


    while current_start < guide_end:

        # --------------------------------------------------
        # Every normal programme is exactly one 3-hour
        # boundary-aligned block.
        # --------------------------------------------------

        original_block_end = (

            current_start

            + timedelta(

                hours=3

            )

        )


        if original_block_end > guide_end:

            original_block_end = guide_end


        # --------------------------------------------------
        # If the verified game starts inside this normal
        # 3-hour block, split the block around the game.
        # --------------------------------------------------

        if (

            game_start

            and

            current_start <= game_start < original_block_end

            and

            not game_has_occurred

        ):

            # --------------------------------------------------
            # PRE-GAME / UPCOMING
            #
            # Everything before the verified game start is
            # explicitly Upcoming.
            # --------------------------------------------------

            if current_start < game_start:

                programme = ET.SubElement(

                    tv,

                    "programme",

                    {

                        "start":

                        current_start.strftime(

                            "%Y%m%d%H%M%S %z"

                        ),

                        "stop":

                        game_start.strftime(

                            "%Y%m%d%H%M%S %z"

                        ),

                        "channel":

                        channel_id

                    }

                )


                title = ET.SubElement(

                    programme,

                    "title"

                )


                title.text = upcoming_title_text


                desc = ET.SubElement(

                    programme,

                    "desc"

                )


                desc.text = description_text


            # --------------------------------------------------
            # REAL GAME
            #
            # ESPN start time + exactly 3 hours.
            #
            # The actual game keeps the normal title.
            # --------------------------------------------------

            actual_game_end = game_end


            if actual_game_end > original_block_end:

                print()

                print(
                    "[GAME CROSSES NORMAL 3-HOUR BOUNDARY]"
                )


                print(
                    f"  Game starts: "
                    f"{game_start}"
                )


                print(
                    f"  Assumed game ends: "
                    f"{actual_game_end}"
                )


                print(
                    f"  Original block ends: "
                    f"{original_block_end}"
                )


            programme = ET.SubElement(

                tv,

                "programme",

                {

                    "start":

                    game_start.strftime(

                        "%Y%m%d%H%M%S %z"

                    ),

                    "stop":

                    actual_game_end.strftime(

                        "%Y%m%d%H%M%S %z"

                    ),

                    "channel":

                    channel_id

                }

            )


            title = ET.SubElement(

                programme,

                "title"

            )


            title.text = title_text


            desc = ET.SubElement(

                programme,

                "desc"

            )


            desc.text = description_text


            # --------------------------------------------------
            # POST-GAME
            #
            # Everything after the game ends is Post Game.
            # --------------------------------------------------

            if actual_game_end < original_block_end:

                programme = ET.SubElement(

                    tv,

                    "programme",

                    {

                        "start":

                        actual_game_end.strftime(

                            "%Y%m%d%H%M%S %z"

                        ),

                        "stop":

                        original_block_end.strftime(

                            "%Y%m%d%H%M%S %z"

                        ),

                        "channel":

                        channel_id

                    }

                )


                title = ET.SubElement(

                    programme,

                    "title"

                )


                title.text = post_game_title_text


                desc = ET.SubElement(

                    programme,

                    "desc"

                )


                desc.text = description_text


                current_start = original_block_end

                game_has_occurred = True

            else:

                # --------------------------------------------------
                # The game crosses one or more normal 3-hour
                # boundaries.
                #
                # The game itself remains one continuous 3-hour
                # programme. Once it ends, create a short
                # Post Game segment up to the next normal
                # 3-hour boundary, then resume normal
                # Post Game blocks.
                # --------------------------------------------------

                current_start = actual_game_end


                game_has_occurred = True


                if current_start < guide_end:

                    elapsed_seconds = (

                        (

                            current_start

                            - guide_start

                        ).total_seconds()

                    )


                    block_seconds = 3 * 60 * 60


                    completed_blocks = (

                        int(

                            elapsed_seconds

                            // block_seconds

                        )

                    )


                    next_boundary = (

                        guide_start

                        + timedelta(

                            seconds=(

                                (

                                    completed_blocks

                                    + 1

                                )

                                * block_seconds

                            )

                        )

                    )


                    if next_boundary > guide_end:

                        next_boundary = guide_end


                    if current_start < next_boundary:

                        programme = ET.SubElement(

                            tv,

                            "programme",

                            {

                                "start":

                                current_start.strftime(

                                    "%Y%m%d%H%M%S %z"

                                ),

                                "stop":

                                next_boundary.strftime(

                                    "%Y%m%d%H%M%S %z"

                                ),

                                "channel":

                                channel_id

                            }

                        )


                        title = ET.SubElement(

                            programme,

                            "title"

                        )


                        title.text = post_game_title_text


                        desc = ET.SubElement(

                            programme,

                            "desc"

                        )


                        desc.text = description_text


                        current_start = next_boundary


            # --------------------------------------------------
            # Game has now been consumed.
            #
            # All subsequent blocks are Post Game.
            # --------------------------------------------------

            game_start = None

            game_end = None


            continue


        # --------------------------------------------------
        # NORMAL 3-HOUR BLOCK
        #
        # FIX:
        #
        # If the game has already finished, this block is
        # Post Game.
        #
        # If the game is still in the future, this block is
        # Upcoming.
        #
        # If there is no verified game, preserve the original
        # title exactly as before.
        # --------------------------------------------------

        current_stop = original_block_end


        programme = ET.SubElement(

            tv,

            "programme",

            {

                "start":

                current_start.strftime(

                    "%Y%m%d%H%M%S %z"

                ),

                "stop":

                current_stop.strftime(

                    "%Y%m%d%H%M%S %z"

                ),

                "channel":

                channel_id

            }

        )


        title = ET.SubElement(

            programme,

            "title"

        )


        if game_has_occurred:

            title.text = post_game_title_text

        elif game_start:

            title.text = upcoming_title_text

        else:

            title.text = title_text


        desc = ET.SubElement(

            programme,

            "desc"

        )


        desc.text = description_text


        current_start = current_stop


# --------------------------------------------------
# Save XMLTV file
# --------------------------------------------------

print()

print(
    "Writing XMLTV file..."
)


tree = ET.ElementTree(

    tv

)


ET.indent(

    tree,

    space="  "

)


tree.write(

    OUTPUT_FILE,

    encoding="utf-8",

    xml_declaration=True

)


# --------------------------------------------------
# Final statistics
# --------------------------------------------------

print()

print(
    "Created:"
)


print(
    OUTPUT_FILE
)


print(

    f"Matched channels: "

    f"{matched}"

)


print(
    "Guide blocks: 3 hours each, split around verified games"
)


print(
    "Verified game duration assumption: 3 hours"
)


print(
    "Guide duration: 3 days"
)


print()

print(
    "ESPN statistics:"
)


print(

    f"Scoreboard API lookups: "

    f"{public_api_lookups}"

)


print(

    f"Verified ESPN event matches: "

    f"{public_api_matches}"

)


print(

    f"Verified ESPN times used: "

    f"{verified_public_times_used}"

)


print(

    f"No ESPN schedule match: "

    f"{no_public_match}"

)


print()

print(
    "Team name conversions:"
)


print(

    f"{team_name_conversions}"

)


print()

print(
    "Matchup logos found:"
)


print(

    f"{logos_found}"

)


print(

    "Matchup logos missing: "

    f"{logos_missing}"

)


print()

print(
    "Detailed matching diagnostics:"
)


for key, value in debug_stats.items():

    print(

        f"{key}: {value}"

    )
