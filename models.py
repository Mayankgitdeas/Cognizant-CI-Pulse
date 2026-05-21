"""
models.py — Data models

The Signal is the core entity. Notice the four `impact_*` fields — these
match the four template questions the analyst fills in. The frontend renders
them as a structured 4-bullet impact analysis.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class Impact:
    HI = "hi"
    MED = "med"
    LO = "lo"


class SourceType:
    OFFICIAL = "official"
    FINANCIAL = "financial"
    MEDIA = "media"


class SignalStatus:
    """Lifecycle states. Public API only returns PUBLISHED.
    Draft → admin review → Published, or → Discarded (soft delete).
    Discarded → Restore → back to Draft."""
    DRAFT = "draft"
    PUBLISHED = "published"
    DISCARDED = "discarded"  # soft-deleted; can be restored


# ─── THE SIGNAL ──────────────────────────────────────────────────────────────

class Signal(SQLModel, table=True):
    """One news item about a competitor, with a Cognizant impact analysis."""

    id: Optional[int] = Field(default=None, primary_key=True)

    # ─── Draft vs Published vs Discarded ──────────────────────────────────
    # Public API only returns PUBLISHED signals. Drafts are scraper output
    # awaiting analyst review. Discarded are soft-deleted items the admin
    # can restore from the discarded view.
    status: str = Field(default=SignalStatus.PUBLISHED, index=True)

    # ─── Article basics (filled by analyst or scraper) ────────────────────
    competitor: str = Field(index=True)
    headline: str
    description: str
    # URL is unique to prevent accidental duplicates from scraping.
    # When admin clicks "Add as draft anyway" on a known duplicate, the URL
    # gets a #cipulse-dup-<timestamp> suffix to satisfy this constraint while
    # keeping the original source URL recoverable by stripping the suffix.
    url: str = Field(unique=True)

    # ─── Classification (filled by analyst) ──────────────────────────────
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    topics: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    impact: str = Field(default=Impact.MED)

    # ─── Source provenance ───────────────────────────────────────────────
    source: str
    source_type: str = Field(default=SourceType.OFFICIAL)
    source_priority: int = Field(default=1)

    # ─── Timestamps ──────────────────────────────────────────────────────
    published_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    discarded_at: Optional[datetime] = Field(default=None)  # soft-delete timestamp

    # ─── Impact analysis ─────────────────────────────────────────────────
    impact_pipeline_risk: Optional[str] = Field(default=None)
    impact_verticals: Optional[str] = Field(default=None)
    impact_opportunity: Optional[str] = Field(default=None)
    impact_intel_gap: Optional[str] = Field(default=None)
    analyst_name: Optional[str] = Field(default=None)
