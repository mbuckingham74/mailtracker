import csv
import io
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Open, TrackedEmail
from ..proxy_detection import detect_proxy_type
from ..time_utils import ensure_utc, format_duration_hours, to_local

VALID_DATE_RANGES = {"7", "30", "90", "all"}
DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


async def build_analytics_context(db: AsyncSession, date_range: str) -> dict:
    date_range = _normalize_date_range(date_range)
    now = datetime.now(timezone.utc)
    cutoff = _get_cutoff(now, date_range)
    tracks, real_opens = await _load_tracks_and_real_opens(db, cutoff)

    total_emails = len(tracks)
    total_real_opens = len(real_opens)
    emails_with_opens = {open_event.tracked_email_id for open_event in real_opens}
    open_rate = (len(emails_with_opens) / total_emails * 100) if total_emails > 0 else 0

    time_to_open_hours = _collect_time_to_open_hours(tracks, real_opens)
    avg_time_to_open = median(time_to_open_hours) if time_to_open_hours else None

    granularity = _get_granularity(date_range)
    time_series_labels, time_series_emails, time_series_opens = _build_time_series(
        tracks,
        real_opens,
        cutoff or datetime(2020, 1, 1, tzinfo=timezone.utc),
        now,
        granularity,
    )

    countries, cities = _build_geography(real_opens)
    top_countries = countries[:10]
    top_cities = cities[:10]
    hour_labels, hour_data = _build_hour_distribution(real_opens)
    dow_data = _build_day_of_week_distribution(real_opens)
    time_bucket_labels, time_bucket_data = _build_time_to_open_buckets(time_to_open_hours)

    return {
        "date_range": date_range,
        "total_emails": total_emails,
        "total_real_opens": total_real_opens,
        "open_rate": round(open_rate, 1),
        "avg_time_to_open": format_duration_hours(avg_time_to_open),
        "time_series_labels": json.dumps(time_series_labels),
        "time_series_emails": json.dumps(time_series_emails),
        "time_series_opens": json.dumps(time_series_opens),
        "country_labels": json.dumps([country for country, _ in top_countries]),
        "country_data": json.dumps([count for _, count in top_countries]),
        "top_cities": top_cities,
        "hour_labels": json.dumps(hour_labels),
        "hour_data": json.dumps(hour_data),
        "dow_labels": json.dumps(DOW_NAMES),
        "dow_data": json.dumps(dow_data),
        "time_bucket_labels": json.dumps(time_bucket_labels),
        "time_bucket_data": json.dumps(time_bucket_data),
    }


async def export_analytics_csv(db: AsyncSession, date_range: str) -> tuple[str, str]:
    date_range = _normalize_date_range(date_range)
    now = datetime.now(timezone.utc)
    cutoff = _get_cutoff(now, date_range)
    tracks, real_opens = await _load_tracks_and_real_opens(db, cutoff)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["=== Analytics Summary ==="])
    writer.writerow(["Date Range", f"Last {date_range} days" if date_range != "all" else "All Time"])
    writer.writerow(["Total Emails Tracked", len(tracks)])
    writer.writerow(["Total Real Opens", len(real_opens)])

    emails_with_opens = {open_event.tracked_email_id for open_event in real_opens}
    open_rate = (len(emails_with_opens) / len(tracks) * 100) if tracks else 0
    writer.writerow(["Open Rate", f"{open_rate:.1f}%"])
    writer.writerow([])

    writer.writerow(["=== Opens by Country ==="])
    writer.writerow(["Country", "Opens"])
    countries, cities = _build_geography(real_opens)
    for country, count in countries:
        writer.writerow([country, count])
    writer.writerow([])

    writer.writerow(["=== Opens by City ==="])
    writer.writerow(["City", "Opens"])
    for city, count in cities[:20]:
        writer.writerow([city, count])
    writer.writerow([])

    writer.writerow(["=== Opens by Hour of Day ==="])
    writer.writerow(["Hour", "Opens"])
    hour_labels, hour_data = _build_hour_distribution(real_opens)
    for label, count in zip(hour_labels, hour_data):
        writer.writerow([label, count])
    writer.writerow([])

    writer.writerow(["=== Opens by Day of Week ==="])
    writer.writerow(["Day", "Opens"])
    dow_data = _build_day_of_week_distribution(real_opens)
    for day_name, count in zip(DOW_NAMES, dow_data):
        writer.writerow([day_name, count])

    output.seek(0)
    export_date = to_local(now).strftime("%Y-%m-%d")
    return f"mailtrack_analytics_{date_range}days_{export_date}.csv", output.getvalue()


def _normalize_date_range(date_range: str) -> str:
    return date_range if date_range in VALID_DATE_RANGES else "30"


def _get_cutoff(now: datetime, date_range: str) -> datetime | None:
    if date_range == "all":
        return None
    return now - timedelta(days=int(date_range))


async def _load_tracks_and_real_opens(
    db: AsyncSession,
    cutoff: datetime | None,
) -> tuple[list[TrackedEmail], list[Open]]:
    track_query = select(TrackedEmail)
    if cutoff is not None:
        track_query = track_query.where(TrackedEmail.created_at >= cutoff)
    track_result = await db.execute(track_query)
    tracks = track_result.scalars().all()

    opens_query = select(Open)
    if cutoff is not None:
        opens_query = opens_query.where(Open.opened_at >= cutoff)
    opens_result = await db.execute(opens_query)
    all_opens = opens_result.scalars().all()

    real_opens = []
    for open_event in all_opens:
        proxy_type = detect_proxy_type(open_event.ip_address, open_event.user_agent or "")
        if proxy_type is None:
            real_opens.append(open_event)

    return tracks, real_opens


def _collect_time_to_open_hours(tracks: list[TrackedEmail], real_opens: list[Open]) -> list[float]:
    first_real_open_times: dict[str, datetime] = {}
    for open_event in real_opens:
        current_first = first_real_open_times.get(open_event.tracked_email_id)
        if current_first is None or open_event.opened_at < current_first:
            first_real_open_times[open_event.tracked_email_id] = open_event.opened_at

    track_created_map = {track.id: track.created_at for track in tracks}
    time_to_open_hours = []

    for track_id, first_open in first_real_open_times.items():
        created = ensure_utc(track_created_map.get(track_id))
        first_open = ensure_utc(first_open)
        if created is None or first_open is None:
            continue

        delta_hours = (first_open - created).total_seconds() / 3600
        if delta_hours > 0:
            time_to_open_hours.append(delta_hours)

    return time_to_open_hours


def _get_granularity(date_range: str) -> str:
    if date_range in {"7", "30"}:
        return "daily"
    if date_range == "90":
        return "weekly"
    return "monthly"


def _build_time_series(
    tracks: list[TrackedEmail],
    real_opens: list[Open],
    start: datetime,
    end: datetime,
    granularity: str,
) -> tuple[list[str], list[int], list[int]]:
    emails_by_date = defaultdict(int)
    opens_by_date = defaultdict(int)

    for track in tracks:
        if track.created_at:
            emails_by_date[_get_date_key(track.created_at, granularity)] += 1

    for open_event in real_opens:
        if open_event.opened_at:
            opens_by_date[_get_date_key(open_event.opened_at, granularity)] += 1

    all_date_keys = _generate_date_keys(start, end, granularity)
    return (
        all_date_keys,
        [emails_by_date.get(date_key, 0) for date_key in all_date_keys],
        [opens_by_date.get(date_key, 0) for date_key in all_date_keys],
    )


def _get_date_key(dt: datetime, granularity: str) -> str:
    local_dt = to_local(dt)
    if local_dt is None:
        return ""

    if granularity == "daily":
        return local_dt.strftime("%Y-%m-%d")
    if granularity == "weekly":
        start_of_week = local_dt - timedelta(days=local_dt.weekday())
        return start_of_week.strftime("%Y-%m-%d")
    return local_dt.strftime("%Y-%m")


def _generate_date_keys(start: datetime, end: datetime, granularity: str) -> list[str]:
    start_local = to_local(start)
    end_local = to_local(end)
    if start_local is None or end_local is None:
        return []

    keys = []
    current = start_local

    if granularity == "daily":
        while current <= end_local:
            keys.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
    elif granularity == "weekly":
        current = current - timedelta(days=current.weekday())
        while current <= end_local:
            keys.append(current.strftime("%Y-%m-%d"))
            current += timedelta(weeks=1)
    else:
        while current <= end_local:
            keys.append(current.strftime("%Y-%m"))
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1)
            else:
                current = current.replace(month=current.month + 1, day=1)

    return keys


def _build_geography(real_opens: list[Open]) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    opens_by_country = defaultdict(int)
    opens_by_city = defaultdict(int)

    for open_event in real_opens:
        country = open_event.country or "Unknown"
        opens_by_country[country] += 1
        if open_event.city:
            opens_by_city[f"{open_event.city}, {country}"] += 1

    sorted_countries = sorted(opens_by_country.items(), key=lambda item: item[1], reverse=True)
    sorted_cities = sorted(opens_by_city.items(), key=lambda item: item[1], reverse=True)
    return sorted_countries, sorted_cities


def _build_hour_distribution(real_opens: list[Open]) -> tuple[list[str], list[int]]:
    opens_by_hour = defaultdict(int)
    for open_event in real_opens:
        local_time = to_local(open_event.opened_at)
        if local_time is not None:
            opens_by_hour[local_time.hour] += 1

    hour_labels = [f"{hour:02d}:00" for hour in range(24)]
    hour_data = [opens_by_hour.get(hour, 0) for hour in range(24)]
    return hour_labels, hour_data


def _build_day_of_week_distribution(real_opens: list[Open]) -> list[int]:
    opens_by_dow = defaultdict(int)
    for open_event in real_opens:
        local_time = to_local(open_event.opened_at)
        if local_time is not None:
            opens_by_dow[local_time.weekday()] += 1
    return [opens_by_dow.get(index, 0) for index in range(7)]


def _build_time_to_open_buckets(time_to_open_hours: list[float]) -> tuple[list[str], list[int]]:
    time_buckets = {
        "<1 hr": 0,
        "1-6 hrs": 0,
        "6-24 hrs": 0,
        "1-3 days": 0,
        "3-7 days": 0,
        ">7 days": 0,
    }

    for hours in time_to_open_hours:
        if hours < 1:
            time_buckets["<1 hr"] += 1
        elif hours < 6:
            time_buckets["1-6 hrs"] += 1
        elif hours < 24:
            time_buckets["6-24 hrs"] += 1
        elif hours < 72:
            time_buckets["1-3 days"] += 1
        elif hours < 168:
            time_buckets["3-7 days"] += 1
        else:
            time_buckets[">7 days"] += 1

    return list(time_buckets.keys()), list(time_buckets.values())
