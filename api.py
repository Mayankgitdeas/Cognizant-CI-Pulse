"""
api.py — REST API endpoints

Namespaces:
  /api/signals          — PUBLIC, read-only (mobile app + web frontend hit these)
                          Only returns published signals; drafts are hidden.
  /api/admin/signals    — ADMIN, requires login (admin panel hits these)
                          Manages published signals (create, edit, delete).
  /api/admin/drafts     — ADMIN, requires login
                          Manages draft signals from scraper (publish, edit, discard).
  /api/admin/scrape     — ADMIN, requires login
                          Triggers scraping of all 5 competitor newsrooms.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import select

from database import get_session
from models import Signal, SignalStatus
from auth import require_admin
from scraper import scrape_all

log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API — only PUBLISHED signals visible
# ═════════════════════════════════════════════════════════════════════════════

public_router = APIRouter(prefix="/api", tags=["public"])


@public_router.get("/signals")
def list_signals(
    days: int = Query(30, ge=1, le=365),
    competitor: Optional[str] = Query(None),
    topic: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    impact: Optional[str] = Query(None),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as s:
        query = (
            select(Signal)
            .where(Signal.status == SignalStatus.PUBLISHED)
            .where(Signal.published_at >= cutoff)
        )
        if competitor:
            query = query.where(Signal.competitor == competitor)
        if impact:
            query = query.where(Signal.impact == impact)
        query = query.order_by(Signal.published_at.desc())
        results = s.exec(query).all()
    if topic:
        results = [r for r in results if topic in r.topics]
    if tag:
        results = [r for r in results if tag in r.tags]
    return results


@public_router.get("/signals/{signal_id}")
def get_signal(signal_id: int):
    with get_session() as s:
        signal = s.exec(
            select(Signal)
            .where(Signal.id == signal_id)
            .where(Signal.status == SignalStatus.PUBLISHED)
        ).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Signal not found")
        return signal


@public_router.get("/health")
def health():
    with get_session() as s:
        pub = len(s.exec(select(Signal).where(Signal.status == SignalStatus.PUBLISHED)).all())
        drf = len(s.exec(select(Signal).where(Signal.status == SignalStatus.DRAFT)).all())
    return {
        "status": "healthy",
        "service": "competitor-pulse",
        "version": "0.3.0-scraper",
        "published_count": pub,
        "draft_count": drf,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ADMIN API — signals CRUD
# ═════════════════════════════════════════════════════════════════════════════

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


class SignalIn(BaseModel):
    competitor: str
    headline: str
    description: str
    url: str
    tags: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    impact: str = "med"
    source: str
    source_type: str = "official"
    source_priority: int = 1
    published_at: datetime
    impact_pipeline_risk: Optional[str] = None
    impact_verticals: Optional[str] = None
    impact_opportunity: Optional[str] = None
    impact_intel_gap: Optional[str] = None
    analyst_name: Optional[str] = None
    status: str = SignalStatus.PUBLISHED


@admin_router.post("/signals", status_code=201)
def create_signal(payload: SignalIn, admin: str = Depends(require_admin)):
    signal = Signal(
        status=payload.status,
        competitor=payload.competitor,
        headline=payload.headline,
        description=payload.description,
        url=payload.url,
        tags=payload.tags,
        topics=payload.topics,
        impact=payload.impact,
        source=payload.source,
        source_type=payload.source_type,
        source_priority=payload.source_priority,
        published_at=payload.published_at,
        impact_pipeline_risk=payload.impact_pipeline_risk,
        impact_verticals=payload.impact_verticals,
        impact_opportunity=payload.impact_opportunity,
        impact_intel_gap=payload.impact_intel_gap,
        analyst_name=payload.analyst_name or admin,
    )
    with get_session() as s:
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} created {signal.status} signal {signal.id}: {signal.headline[:60]}")
    return signal


@admin_router.put("/signals/{signal_id}")
def update_signal(signal_id: int, payload: SignalIn, admin: str = Depends(require_admin)):
    with get_session() as s:
        signal = s.exec(select(Signal).where(Signal.id == signal_id)).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Signal not found")
        for field, value in payload.model_dump().items():
            setattr(signal, field, value)
        signal.updated_at = datetime.now(timezone.utc)
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} updated signal {signal_id}")
    return signal


@admin_router.delete("/signals/{signal_id}")
def delete_signal(
    signal_id: int,
    hard: bool = Query(False, description="If true, permanently delete; otherwise soft-delete (move to Discarded)"),
    admin: str = Depends(require_admin),
):
    """Soft-delete by default (status='discarded'). Pass ?hard=true to permanently remove."""
    with get_session() as s:
        signal = s.exec(select(Signal).where(Signal.id == signal_id)).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Signal not found")
        if hard:
            s.delete(signal)
            s.commit()
            log.info(f"Admin {admin} HARD-deleted signal {signal_id}")
            return {"hard_deleted": signal_id}
        signal.status = SignalStatus.DISCARDED
        signal.discarded_at = datetime.now(timezone.utc)
        signal.updated_at = datetime.now(timezone.utc)
        s.add(signal)
        s.commit()
    log.info(f"Admin {admin} discarded signal {signal_id}")
    return {"discarded": signal_id}


@admin_router.post("/signals/{signal_id}/restore")
def restore_signal(signal_id: int, admin: str = Depends(require_admin)):
    """Restore a discarded signal back to draft state for analyst review."""
    with get_session() as s:
        signal = s.exec(
            select(Signal)
            .where(Signal.id == signal_id)
            .where(Signal.status == SignalStatus.DISCARDED)
        ).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Discarded signal not found")
        signal.status = SignalStatus.DRAFT
        signal.discarded_at = None
        signal.updated_at = datetime.now(timezone.utc)
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} restored signal {signal_id}")
    return signal


@admin_router.get("/discarded")
def list_discarded(admin: str = Depends(require_admin)):
    """List soft-deleted signals (most recently discarded first)."""
    with get_session() as s:
        return s.exec(
            select(Signal)
            .where(Signal.status == SignalStatus.DISCARDED)
            .order_by(Signal.discarded_at.desc())
        ).all()


@admin_router.get("/signals")
def list_all_signals(admin: str = Depends(require_admin)):
    """List PUBLISHED signals only — drafts visible via /drafts."""
    with get_session() as s:
        return s.exec(
            select(Signal)
            .where(Signal.status == SignalStatus.PUBLISHED)
            .order_by(Signal.published_at.desc())
        ).all()


# ═════════════════════════════════════════════════════════════════════════════
# DRAFTS — pre-filled by scraper, await analyst review
# ═════════════════════════════════════════════════════════════════════════════

@admin_router.get("/drafts")
def list_drafts(admin: str = Depends(require_admin)):
    with get_session() as s:
        return s.exec(
            select(Signal)
            .where(Signal.status == SignalStatus.DRAFT)
            .order_by(Signal.published_at.desc())
        ).all()


@admin_router.post("/drafts/{signal_id}/publish")
def publish_draft(signal_id: int, payload: SignalIn, admin: str = Depends(require_admin)):
    """Convert a draft to published with analyst's added impact analysis."""
    with get_session() as s:
        signal = s.exec(
            select(Signal).where(Signal.id == signal_id).where(Signal.status == SignalStatus.DRAFT)
        ).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Draft not found")
        for field, value in payload.model_dump().items():
            setattr(signal, field, value)
        signal.status = SignalStatus.PUBLISHED
        signal.updated_at = datetime.now(timezone.utc)
        if not signal.analyst_name:
            signal.analyst_name = admin
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} published draft {signal_id}: {signal.headline[:60]}")
    return signal


# ═════════════════════════════════════════════════════════════════════════════
# SCRAPER — fetches new articles from 5 P1 newsrooms + P2 news portals
# ═════════════════════════════════════════════════════════════════════════════

class DuplicateAdd(BaseModel):
    """Force-add a known duplicate scraped article as a new draft."""
    competitor: str
    headline: str
    description: str = ""
    url: str
    published_at: str        # ISO date
    source: str
    source_priority: int = 1
    suggested_tag: Optional[str] = None
    suggested_topics: list[str] = Field(default_factory=list)
    suggested_impact: str = "med"


@admin_router.post("/drafts/add_duplicate")
def add_duplicate_as_draft(payload: DuplicateAdd, admin: str = Depends(require_admin)):
    """Create a new draft from a scraped article that was flagged as duplicate.

    The URL gets a #cipulse-dup-<timestamp> suffix appended to satisfy the
    unique constraint while keeping the original source URL recoverable.
    """
    import time as _time
    suffix = f"#cipulse-dup-{int(_time.time() * 1000)}"
    unique_url = payload.url.split("#")[0] + suffix  # strip any existing fragment then add ours

    try:
        published_at = datetime.fromisoformat(payload.published_at.replace("Z", "+00:00"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid published_at format")

    signal = Signal(
        status=SignalStatus.DRAFT,
        competitor=payload.competitor,
        headline=payload.headline,
        description=payload.description or payload.headline,
        url=unique_url,
        tags=[payload.suggested_tag] if payload.suggested_tag else [],
        topics=payload.suggested_topics,
        impact=payload.suggested_impact,
        source=payload.source,
        source_type="official" if payload.source_priority == 1 else "media",
        source_priority=payload.source_priority,
        published_at=published_at,
    )
    with get_session() as s:
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} force-added duplicate {signal.id}: {signal.headline[:60]}")
    return signal


@admin_router.post("/scrape")
async def trigger_scrape(
    days: int = Query(14, ge=1, le=180),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    admin: str = Depends(require_admin),
):
    """Scrape all newsrooms (P1) and news portals (P2); create drafts.

    Duplicate detection looks at ACTIVE signals (drafts + published).
    Discarded signals don't count as duplicates — re-scraping can resurface them.
    """
    log.info(f"Admin {admin} triggered scrape (days={days}, from={from_date}, to={to_date})")

    parsed_from = None
    parsed_to = None
    if from_date:
        try:
            parsed_from = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format. Use YYYY-MM-DD.")
    if to_date:
        try:
            parsed_to = datetime.fromisoformat(to_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format. Use YYYY-MM-DD.")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date.")

    # Hardening: catch *anything* from scraper or DB and report a useful error
    # so the admin UI gets a real message instead of an opaque 500.
    try:
        result = await scrape_all(from_date=parsed_from, to_date=parsed_to, days_window=days)
    except Exception as e:
        import traceback
        log.error(f"scrape_all crashed: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Scraper crashed: {type(e).__name__}: {str(e)[:300]}"
        )

    new_drafts = 0
    duplicates = []
    errors = 0

    try:
      with get_session() as s:
        for article in result.articles:
            # Look for active (non-discarded) matching URL
            existing = s.exec(
                select(Signal)
                .where(Signal.url == article.url)
                .where(Signal.status != SignalStatus.DISCARDED)
            ).first()
            if existing:
                duplicates.append({
                    "scraped_headline": article.headline,
                    "scraped_url": article.url,
                    "scraped_competitor": article.competitor,
                    "scraped_published_at": article.published_at.isoformat(),
                    "scraped_source": article.source,
                    "scraped_source_priority": article.source_priority,
                    "scraped_suggested_tag": article.suggested_tag,
                    "scraped_suggested_topics": article.suggested_topics or [],
                    "scraped_suggested_impact": article.suggested_impact,
                    "scraped_description": article.description,
                    "existing_id": existing.id,
                    "existing_status": existing.status,
                    "existing_headline": existing.headline,
                })
                continue
            try:
                signal = Signal(
                    status=SignalStatus.DRAFT,
                    competitor=article.competitor,
                    headline=article.headline,
                    description=article.description,
                    url=article.url,
                    tags=[article.suggested_tag] if article.suggested_tag else [],
                    topics=article.suggested_topics or [],
                    impact=article.suggested_impact,
                    source=article.source,
                    source_type="official" if article.source_priority == 1 else "media",
                    source_priority=article.source_priority,
                    published_at=article.published_at,
                )
                s.add(signal)
                new_drafts += 1
            except Exception as e:
                log.warning(f"Could not save article {article.url}: {e}")
                errors += 1
        s.commit()
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        log.error(f"DB write during scrape crashed: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"DB error during scrape: {type(e).__name__}: {str(e)[:300]}"
        )

    log.info(
        f"Scrape: {new_drafts} new drafts, {len(duplicates)} duplicates, "
        f"{errors} errors, {len(result.failed_sources)} sources failed"
    )

    return {
        "new_drafts": new_drafts,
        "duplicates_skipped": len(duplicates),
        "duplicates": duplicates,
        "errors": errors,
        "failed_sources": result.failed_sources,
        "duration_seconds": round(result.duration_seconds, 1),
        "total_articles_found": len(result.articles),
        "date_range_used": {
            "from": (parsed_from or (datetime.now(timezone.utc) - timedelta(days=days))).isoformat(),
            "to": (parsed_to or datetime.now(timezone.utc)).isoformat(),
        },
    }
