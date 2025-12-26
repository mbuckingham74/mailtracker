from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, Boolean
from sqlalchemy.sql import func
import os

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Required environment variable DATABASE_URL is not set")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


class TrackedEmail(Base):
    __tablename__ = "tracked_emails"

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


class Open(Base):
    __tablename__ = "opens"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tracked_email_id = Column(String(36), ForeignKey("tracked_emails.id", ondelete="CASCADE"), nullable=False)
    opened_at = Column(DateTime, server_default=func.now())
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    referer = Column(Text, nullable=True)
    country = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)


async def get_db():
    async with async_session() as session:
        yield session
