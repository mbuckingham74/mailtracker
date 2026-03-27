from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import TrackedEmail
from ..time_utils import ensure_utc, format_duration_hours, format_time_ago
from .open_activity import TrackRealOpenSummary, load_real_open_summaries

ITEMS_PER_PAGE = 25
VALID_SORTS = {"email", "sent", "opened", "rate", "last_open", "score"}
VALID_ORDERS = {"asc", "desc"}


@dataclass(frozen=True)
class RecipientTrackSnapshot:
    id: str
    recipient: str | None


@dataclass(frozen=True)
class RecipientDetailTrackSnapshot:
    id: str
    recipient: str | None
    subject: str | None
    created_at: datetime | None


async def build_recipients_context(
    db: AsyncSession,
    *,
    search: str,
    sort: str,
    order: str,
    page: int,
) -> dict:
    now = datetime.now(timezone.utc)
    raw_search = search.strip()
    tracks, real_open_summaries = await _load_recipient_tracks_and_real_open_summaries(
        db,
        search=raw_search,
    )

    recipient_list = _build_recipient_list(tracks, real_open_summaries, now)
    search = raw_search.lower()
    if search:
        recipient_list = [recipient for recipient in recipient_list if search in recipient["email_lower"]]

    sort = sort if sort in VALID_SORTS else "score"
    order = order if order in VALID_ORDERS else "desc"
    recipient_list.sort(key=_get_sort_key(sort), reverse=(order == "desc"))

    total_items = len(recipient_list)
    total_pages = max(1, (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = min(max(page, 1), total_pages)
    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE

    return {
        "recipients": recipient_list[start_idx:end_idx],
        "search": search,
        "sort": sort,
        "order": order,
        "page": page,
        "total_pages": total_pages,
        "total_items": total_items,
    }


async def build_recipient_detail_context(db: AsyncSession, email: str) -> dict:
    now = datetime.now(timezone.utc)
    email_lower = email.lower()
    search_pattern = f"%{email}%"

    result = await db.execute(
        select(
            TrackedEmail.id,
            TrackedEmail.recipient,
            TrackedEmail.subject,
            TrackedEmail.created_at,
        )
        .where(TrackedEmail.recipient.ilike(search_pattern))
        .order_by(TrackedEmail.created_at.desc())
    )
    candidate_tracks = [
        RecipientDetailTrackSnapshot(
            id=track_id,
            recipient=recipient,
            subject=subject,
            created_at=created_at,
        )
        for track_id, recipient, subject, created_at in result
    ]

    tracks = []
    display_email = email
    for track in candidate_tracks:
        recipient_candidates = _split_recipient_emails(track.recipient)
        if email_lower not in {candidate_lower for _, candidate_lower in recipient_candidates}:
            continue

        tracks.append(track)
        if display_email == email:
            for candidate, candidate_lower in recipient_candidates:
                if candidate_lower == email_lower:
                    display_email = candidate
                    break

    if not tracks:
        raise HTTPException(status_code=404, detail="Recipient not found")

    track_ids = [track.id for track in tracks]
    real_open_summaries = await load_real_open_summaries(db, track_ids=track_ids)

    opened = 0
    last_open = None
    time_to_open_hours = []
    email_history = []

    for track in tracks:
        real_open_summary = real_open_summaries.get(track.id)
        was_opened = real_open_summary is not None and real_open_summary.count > 0
        first_open = real_open_summary.first_open_at if real_open_summary else None
        latest_open = real_open_summary.last_open_at if real_open_summary else None

        if was_opened:
            opened += 1
            if latest_open is not None and (last_open is None or latest_open > last_open):
                last_open = latest_open

            created_at = ensure_utc(track.created_at)
            if created_at is not None and first_open is not None:
                delta_hours = (first_open - created_at).total_seconds() / 3600
                if delta_hours > 0:
                    time_to_open_hours.append(delta_hours)

        email_history.append({
            "track": track,
            "was_opened": was_opened,
            "first_open": first_open,
            "first_open_display": format_time_ago(first_open, now) if first_open else None,
            "open_count": real_open_summary.count if real_open_summary else 0,
        })

    sent = len(tracks)
    open_rate = (opened / sent * 100) if sent > 0 else 0
    score = _calculate_engagement_score(sent, opened, last_open, now)
    avg_time = median(time_to_open_hours) if time_to_open_hours else None

    return {
        "email": display_email,
        "sent": sent,
        "opened": opened,
        "open_rate": round(open_rate, 1),
        "avg_time_to_open": format_duration_hours(avg_time),
        "score": score,
        "score_label": _get_engagement_label(score),
        "last_open": last_open,
        "last_open_display": format_time_ago(last_open, now),
        "email_history": email_history,
    }


async def _load_recipient_tracks_and_real_open_summaries(
    db: AsyncSession,
    *,
    search: str = "",
) -> tuple[list[RecipientTrackSnapshot], dict[str, TrackRealOpenSummary]]:
    track_query = select(TrackedEmail.id, TrackedEmail.recipient)
    if search:
        track_query = track_query.where(TrackedEmail.recipient.ilike(f"%{search}%"))

    track_result = await db.execute(track_query)
    tracks = [
        RecipientTrackSnapshot(id=track_id, recipient=recipient)
        for track_id, recipient in track_result
    ]
    real_open_summaries = await load_real_open_summaries(db, track_ids=[track.id for track in tracks])
    return tracks, real_open_summaries


def _build_recipient_list(
    tracks: list[RecipientTrackSnapshot],
    real_open_summaries: dict[str, TrackRealOpenSummary],
    now: datetime,
) -> list[dict]:
    recipients: dict[str, dict] = {}

    for track in tracks:
        for display_email, email in _split_recipient_emails(track.recipient):
            if not email:
                continue

            recipient_data = recipients.setdefault(
                email,
                {
                    "email": email,
                    "display_email": display_email,
                    "sent": 0,
                    "opened": 0,
                    "last_open": None,
                },
            )

            recipient_data["sent"] += 1

            real_open_summary = real_open_summaries.get(track.id)
            if real_open_summary is None or real_open_summary.count == 0:
                continue

            recipient_data["opened"] += 1
            last_open = real_open_summary.last_open_at
            if last_open and (
                recipient_data["last_open"] is None
                or last_open > recipient_data["last_open"]
            ):
                recipient_data["last_open"] = last_open

    recipient_list = []
    for email, data in recipients.items():
        open_rate = (data["opened"] / data["sent"] * 100) if data["sent"] > 0 else 0
        score = _calculate_engagement_score(data["sent"], data["opened"], data["last_open"], now)
        recipient_list.append({
            "email": data["display_email"],
            "email_lower": email,
            "sent": data["sent"],
            "opened": data["opened"],
            "open_rate": round(open_rate, 1),
            "last_open": data["last_open"],
            "last_open_display": format_time_ago(data["last_open"], now),
            "score": score,
            "score_label": _get_engagement_label(score),
        })

    return recipient_list


def _split_recipient_emails(recipient: str | None) -> list[tuple[str, str]]:
    if not recipient:
        return []

    recipients: list[tuple[str, str]] = []
    for candidate in recipient.split(","):
        display_email = candidate.strip()
        if not display_email:
            continue
        recipients.append((display_email, display_email.lower()))
    return recipients


def _get_sort_key(sort: str):
    return {
        "email": lambda item: item["email_lower"],
        "sent": lambda item: item["sent"],
        "opened": lambda item: item["opened"],
        "rate": lambda item: item["open_rate"],
        "last_open": lambda item: item["last_open"] or datetime.min.replace(tzinfo=timezone.utc),
        "score": lambda item: item["score"],
    }.get(sort, lambda item: item["score"])


def _calculate_engagement_score(sent: int, opened: int, last_open: datetime | None, now: datetime) -> int:
    if sent == 0:
        return 0

    open_rate = opened / sent
    open_rate_score = open_rate * 50

    if last_open is not None:
        last_open = ensure_utc(last_open)
        days_ago = (now - last_open).days
        if days_ago <= 7:
            recency_score = 25
        elif days_ago <= 90:
            recency_score = 25 * (1 - (days_ago - 7) / 83)
        else:
            recency_score = 0
    else:
        recency_score = 0

    consistency_score = open_rate * 25 if sent >= 3 else open_rate * 15
    return round(open_rate_score + recency_score + consistency_score)


def _get_engagement_label(score: int) -> str:
    if score >= 80:
        return "Highly Engaged"
    if score >= 60:
        return "Engaged"
    if score >= 40:
        return "Moderate"
    if score >= 20:
        return "Low"
    return "Unengaged"
