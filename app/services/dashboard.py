import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Open, TrackedEmail
from ..open_snapshot import StoredOpenSnapshot, build_open_snapshot
from ..time_utils import ensure_utc, to_local
from ..urls import get_pixel_url

ITEMS_PER_PAGE = 25
VALID_FILTERS = {"all", "opened", "unopened"}
VALID_DATE_RANGES = {"all", "7", "30", "90"}


@dataclass(frozen=True)
class DashboardTrackSnapshot:
    id: str
    recipient: str | None
    subject: str | None
    notes: str | None
    message_group_id: str | None
    created_at: datetime | None
    pinned: bool = False


@dataclass(frozen=True)
class DetailTrackSnapshot:
    id: str
    recipient: str | None
    subject: str | None
    notes: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class OpenSnapshot(StoredOpenSnapshot):
    tracked_email_id: str


@dataclass
class DashboardOpenSummary:
    open_count: int = 0
    real_open_count: int = 0
    first_open: datetime | None = None
    first_real_open: datetime | None = None
    first_proxy_open: datetime | None = None
    first_proxy_type: str | None = None


async def build_dashboard_context(
    db: AsyncSession,
    *,
    filter_value: str,
    search: str,
    date_range: str,
    page: int,
) -> dict:
    filter_value = filter_value if filter_value in VALID_FILTERS else "all"
    date_range = date_range if date_range in VALID_DATE_RANGES else "all"
    page = max(page, 1)

    search = search.strip()
    tracks = await _load_dashboard_track_snapshots(
        db,
        search=search,
        date_range=date_range,
    )
    open_summaries = await _load_dashboard_open_summaries(db, [track.id for track in tracks])

    groups: dict[str, list[dict]] = {}
    ungrouped: list[dict] = []

    for track in tracks:
        track_data = _build_track_summary(track, open_summaries.get(track.id))

        if filter_value == "opened" and track_data["real_open_count"] == 0:
            continue
        if filter_value == "unopened" and track_data["real_open_count"] > 0:
            continue

        if track.message_group_id:
            groups.setdefault(track.message_group_id, []).append(track_data)
        else:
            ungrouped.append(track_data)

    tracks_with_counts = _build_grouped_dashboard_items(groups, ungrouped)
    tracks_with_counts.sort(key=_dashboard_sort_key)

    total_items = len(tracks_with_counts)
    total_pages = max(1, (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = min(page, total_pages)

    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE

    query_params = {}
    if filter_value != "all":
        query_params["filter"] = filter_value
    if search:
        query_params["search"] = search
    if date_range != "all":
        query_params["date_range"] = date_range

    return {
        "tracks": tracks_with_counts[start_idx:end_idx],
        "filter": filter_value,
        "search": search,
        "date_range": date_range,
        "page": page,
        "total_pages": total_pages,
        "total_items": total_items,
        "query_params": query_params,
    }


async def build_detail_context(db: AsyncSession, track_id: str) -> dict:
    result = await db.execute(
        select(
            TrackedEmail.id,
            TrackedEmail.recipient,
            TrackedEmail.subject,
            TrackedEmail.notes,
            TrackedEmail.created_at,
        ).where(TrackedEmail.id == track_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Track not found")
    track = DetailTrackSnapshot(
        id=row[0],
        recipient=row[1],
        subject=row[2],
        notes=row[3],
        created_at=row[4],
    )

    opens_asc = await _load_track_opens_asc(db, track_id)
    proxy_opens, real_opens = _partition_proxy_opens(opens_asc)

    first_known_proxy = next(
        (
            (open_event, proxy_type)
            for open_event, proxy_type in proxy_opens
            if proxy_type is not None
        ),
        None,
    )
    first_proxy_open = first_known_proxy[0].opened_at if first_known_proxy else None
    first_proxy_type = first_known_proxy[1] if first_known_proxy else None
    opens = list(reversed(opens_asc))

    pixel_url = get_pixel_url(track.id)
    html_snippet = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="" />'

    gmail_search_parts = ["in:sent"]
    if track.recipient:
        first_recipient = track.recipient.split(",")[0].strip()
        gmail_search_parts.append(f"to:{first_recipient}")
    if track.subject:
        gmail_search_parts.append(f"subject:{track.subject}")
    gmail_search_query = " ".join(gmail_search_parts)

    return {
        "track": track,
        "opens": opens,
        "real_open_count": len(real_opens),
        "first_proxy_open": first_proxy_open,
        "first_proxy_type": first_proxy_type,
        "pixel_url": pixel_url,
        "html_snippet": html_snippet,
        "gmail_search_url": f"https://mail.google.com/mail/u/0/#search/{quote(gmail_search_query)}",
    }


async def delete_track(db: AsyncSession, track_id: str) -> None:
    await db.execute(delete(TrackedEmail).where(TrackedEmail.id == track_id))
    await db.commit()


async def toggle_track_pin(db: AsyncSession, track_id: str) -> None:
    result = await db.execute(
        select(TrackedEmail.pinned).where(TrackedEmail.id == track_id)
    )
    pinned = result.scalar_one_or_none()
    if pinned is None:
        return

    await db.execute(
        update(TrackedEmail)
        .where(TrackedEmail.id == track_id)
        .values(pinned=not bool(pinned))
    )
    await db.commit()


async def update_track_notes(db: AsyncSession, track_id: str, notes: str) -> None:
    await db.execute(
        update(TrackedEmail)
        .where(TrackedEmail.id == track_id)
        .values(notes=notes.strip() if notes else None)
    )
    await db.commit()


async def export_tracks_csv(db: AsyncSession) -> tuple[str, str]:
    tracks = await _load_dashboard_track_snapshots(db)
    opens_by_track_id = await _load_track_opens_map_asc(db, [track.id for track in tracks])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "email_id",
        "recipient",
        "subject",
        "email_created_at",
        "opened_at",
        "ip_address",
        "country",
        "city",
        "user_agent",
        "proxy_type",
        "is_real_open",
    ])

    for track in tracks:
        opens = opens_by_track_id.get(track.id, [])
        email_created = to_local(track.created_at).strftime("%Y-%m-%d %H:%M:%S %Z") if track.created_at else ""

        for open_event in opens:
            opened_at = to_local(open_event.opened_at).strftime("%Y-%m-%d %H:%M:%S %Z") if open_event.opened_at else ""
            writer.writerow([
                track.id,
                track.recipient or "",
                track.subject or "",
                email_created,
                opened_at,
                open_event.ip_address or "",
                open_event.country or "",
                open_event.city or "",
                open_event.user_agent or "",
                open_event.proxy_type or "",
                "yes" if open_event.is_real_open else "no",
            ])

    output.seek(0)
    export_date = to_local(datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return f"mailtrack_export_{export_date}.csv", output.getvalue()


async def _load_track_opens_asc(db: AsyncSession, track_id: str) -> list[OpenSnapshot]:
    return (await _load_track_opens_map_asc(db, [track_id])).get(track_id, [])


async def _load_dashboard_track_snapshots(
    db: AsyncSession,
    *,
    search: str = "",
    date_range: str = "all",
) -> list[DashboardTrackSnapshot]:
    query = (
        select(
            TrackedEmail.id,
            TrackedEmail.recipient,
            TrackedEmail.subject,
            TrackedEmail.notes,
            TrackedEmail.message_group_id,
            TrackedEmail.created_at,
            TrackedEmail.pinned,
        )
        .order_by(TrackedEmail.created_at.desc())
    )

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                TrackedEmail.recipient.ilike(search_pattern),
                TrackedEmail.subject.ilike(search_pattern),
            )
        )

    if date_range != "all":
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(date_range))
        query = query.where(TrackedEmail.created_at >= cutoff)

    result = await db.execute(query)
    return [
        DashboardTrackSnapshot(
            id=track_id,
            recipient=recipient,
            subject=subject,
            notes=notes,
            message_group_id=message_group_id,
            created_at=created_at,
            pinned=bool(pinned),
        )
        for track_id, recipient, subject, notes, message_group_id, created_at, pinned in result
    ]


async def _load_track_opens_map_asc(
    db: AsyncSession,
    track_ids: list[str],
) -> dict[str, list[OpenSnapshot]]:
    if not track_ids:
        return {}

    opens_result = await db.execute(
        select(
            Open.tracked_email_id,
            Open.opened_at,
            Open.is_real_open,
            Open.proxy_type,
            Open.ip_address,
            Open.user_agent,
            Open.country,
            Open.city,
        )
        .where(Open.tracked_email_id.in_(track_ids))
        .order_by(Open.tracked_email_id.asc(), Open.opened_at.asc(), Open.id.asc())
    )

    opens_by_track_id: dict[str, list[OpenSnapshot]] = defaultdict(list)
    for (
        tracked_email_id,
        opened_at,
        is_real_open,
        proxy_type,
        ip_address,
        user_agent,
        country,
        city,
    ) in opens_result:
        opens_by_track_id[tracked_email_id].append(
            build_open_snapshot(
                OpenSnapshot,
                tracked_email_id=tracked_email_id,
                opened_at=opened_at,
                ip_address=ip_address,
                user_agent=user_agent,
                country=country,
                city=city,
                proxy_type=proxy_type,
                is_real_open=is_real_open,
            )
        )

    return dict(opens_by_track_id)


async def _load_dashboard_open_summaries(
    db: AsyncSession,
    track_ids: list[str],
) -> dict[str, DashboardOpenSummary]:
    if not track_ids:
        return {}

    result = await db.execute(
        select(
            Open.tracked_email_id,
            Open.opened_at,
            Open.is_real_open,
            Open.proxy_type,
        )
        .where(Open.tracked_email_id.in_(track_ids))
        .order_by(Open.tracked_email_id.asc(), Open.opened_at.asc(), Open.id.asc())
    )

    summaries: dict[str, DashboardOpenSummary] = {}
    for tracked_email_id, opened_at, is_real_open, proxy_type in result:
        summary = summaries.setdefault(tracked_email_id, DashboardOpenSummary())
        summary.open_count += 1
        if summary.first_open is None:
            summary.first_open = opened_at

        if not is_real_open:
            if summary.first_proxy_open is None and proxy_type is not None:
                summary.first_proxy_open = opened_at
                summary.first_proxy_type = proxy_type
            continue

        summary.real_open_count += 1
        if summary.first_real_open is None:
            summary.first_real_open = opened_at

    return summaries


def _partition_proxy_opens(
    opens: list[OpenSnapshot],
) -> tuple[list[tuple[OpenSnapshot, str | None]], list[OpenSnapshot]]:
    proxy_opens: list[tuple[OpenSnapshot, str | None]] = []
    real_opens: list[OpenSnapshot] = []

    for open_event in opens:
        if not open_event.is_real_open:
            proxy_opens.append((open_event, open_event.proxy_type))
        else:
            real_opens.append(open_event)

    return proxy_opens, real_opens


def _build_track_summary(
    track: DashboardTrackSnapshot,
    open_summary: DashboardOpenSummary | None,
) -> dict:
    open_summary = open_summary or DashboardOpenSummary()

    return {
        "track": track,
        "open_count": open_summary.open_count,
        "real_open_count": open_summary.real_open_count,
        "first_open": open_summary.first_open,
        "first_real_open": open_summary.first_real_open,
        "first_proxy_open": open_summary.first_proxy_open,
        "first_proxy_type": open_summary.first_proxy_type,
        "pinned": track.pinned or False,
    }


def _build_grouped_dashboard_items(groups: dict[str, list[dict]], ungrouped: list[dict]) -> list[dict]:
    items = []
    sorted_groups = sorted(
        groups.items(),
        key=lambda item: min(track_data["track"].created_at for track_data in item[1]),
        reverse=True,
    )

    for group_id, group_tracks in sorted_groups:
        group_tracks.sort(key=lambda track_data: track_data["track"].created_at)
        proxy_tracks = [
            (track_data["first_proxy_open"], track_data["first_proxy_type"])
            for track_data in group_tracks
            if track_data["first_proxy_open"]
        ]
        first_proxy = min(proxy_tracks, key=lambda item: item[0]) if proxy_tracks else (None, None)
        items.append({
            "is_group": True,
            "group_id": group_id,
            "subject": group_tracks[0]["track"].subject,
            "created_at": group_tracks[0]["track"].created_at,
            "recipients": group_tracks,
            "total_opens": sum(track_data["open_count"] for track_data in group_tracks),
            "total_real_opens": sum(track_data["real_open_count"] for track_data in group_tracks),
            "first_open": min(
                (track_data["first_open"] for track_data in group_tracks if track_data["first_open"]),
                default=None,
            ),
            "first_real_open": min(
                (
                    track_data["first_real_open"]
                    for track_data in group_tracks
                    if track_data["first_real_open"]
                ),
                default=None,
            ),
            "first_proxy_open": first_proxy[0],
            "first_proxy_type": first_proxy[1],
            "pinned": any(track_data["pinned"] for track_data in group_tracks),
        })

    for track_data in ungrouped:
        items.append({"is_group": False, **track_data})

    return items


def _dashboard_sort_key(item: dict) -> tuple[bool, float]:
    if item.get("is_group"):
        created_at = item.get("created_at")
    else:
        track = item.get("track")
        created_at = track.created_at if track else None

    created_at = ensure_utc(created_at)
    timestamp = created_at.timestamp() if created_at else 0
    return (not item.get("pinned", False), -timestamp)
