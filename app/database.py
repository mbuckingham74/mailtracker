import logging

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func

from .config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

STARTUP_MIGRATIONS = {
    "tracked_emails": {
        "hot_notified_at": "ALTER TABLE tracked_emails ADD COLUMN hot_notified_at DATETIME NULL",
        "revived_notified_at": "ALTER TABLE tracked_emails ADD COLUMN revived_notified_at DATETIME NULL",
    },
    "opens": {
        "proxy_type": "ALTER TABLE opens ADD COLUMN proxy_type VARCHAR(32) NULL",
        "is_real_open": "ALTER TABLE opens ADD COLUMN is_real_open BOOLEAN NULL",
    }
}

STARTUP_INDEX_MIGRATIONS = {
    "tracked_emails": {
        "ix_tracked_emails_created_at": (
            "CREATE INDEX ix_tracked_emails_created_at ON tracked_emails (created_at)"
        ),
    },
    "opens": {
        "ix_opens_opened_at_id": (
            "CREATE INDEX ix_opens_opened_at_id ON opens (opened_at, id)"
        ),
        "ix_opens_tracked_email_id_opened_at_id": (
            "CREATE INDEX ix_opens_tracked_email_id_opened_at_id "
            "ON opens (tracked_email_id, opened_at, id)"
        ),
    },
}


class TrackedEmail(Base):
    __tablename__ = "tracked_emails"
    __table_args__ = (
        Index("ix_tracked_emails_created_at", "created_at"),
    )

    id = Column(String(36), primary_key=True)
    recipient = Column(String(255), nullable=True)
    subject = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    message_group_id = Column(String(36), nullable=True, index=True)  # Groups multiple recipients from same email
    created_at = Column(DateTime, server_default=func.now())
    notified_at = Column(DateTime, nullable=True)  # When email notification was sent for first real open
    pinned = Column(Boolean, default=False, nullable=False)  # Pin important emails to top
    followup_notified_at = Column(DateTime, nullable=True)  # When follow-up reminder was sent for unopened email
    hot_notified_at = Column(DateTime, nullable=True)  # When "hot conversation" notification was sent (3+ opens in 24h)
    revived_notified_at = Column(DateTime, nullable=True)  # When "old conversation revived" notification was sent (open 2+ weeks after first)


class Open(Base):
    __tablename__ = "opens"
    __table_args__ = (
        Index("ix_opens_opened_at_id", "opened_at", "id"),
        Index("ix_opens_tracked_email_id_opened_at_id", "tracked_email_id", "opened_at", "id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracked_email_id = Column(String(36), ForeignKey("tracked_emails.id", ondelete="CASCADE"), nullable=False)
    opened_at = Column(DateTime, server_default=func.now())
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    referer = Column(Text, nullable=True)
    country = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    proxy_type = Column(String(32), nullable=True)
    is_real_open = Column(Boolean, nullable=True)


async def get_db():
    async with async_session() as session:
        yield session


async def init_database():
    """Create missing tables and apply lightweight compatibility migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        for table_name, migrations in STARTUP_MIGRATIONS.items():
            result = await conn.execute(
                text(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = :table_name
                    """
                ),
                {"table_name": table_name},
            )
            existing_columns = {row[0] for row in result}

            for column_name, ddl in migrations.items():
                if column_name in existing_columns:
                    continue

                logger.warning(
                    "Applying startup migration for %s.%s",
                    table_name,
                    column_name,
                )
                await conn.execute(text(ddl))

        for table_name, migrations in STARTUP_INDEX_MIGRATIONS.items():
            result = await conn.execute(
                text(
                    """
                    SELECT DISTINCT INDEX_NAME
                    FROM INFORMATION_SCHEMA.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = :table_name
                    """
                ),
                {"table_name": table_name},
            )
            existing_indexes = {row[0] for row in result}

            for index_name, ddl in migrations.items():
                if index_name in existing_indexes:
                    continue

                logger.warning(
                    "Applying startup index migration for %s.%s",
                    table_name,
                    index_name,
                )
                await conn.execute(text(ddl))


async def check_database_health() -> tuple[bool, str | None]:
    """Verify database connectivity and ORM/schema compatibility."""
    try:
        async with async_session() as session:
            await session.execute(select(TrackedEmail.id).limit(1))
            await session.execute(select(Open.id).limit(1))
        return True, None
    except Exception as exc:
        return False, str(exc)
