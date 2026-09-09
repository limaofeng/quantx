"""Global historical-download policy, with optimistic update version."""

from sqlalchemy import JSON, Column, Integer, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class HistoryDownloadSettingsRecord(Base, TimestampMixin):
  __tablename__ = "history_download_settings"
  id = Column(String(32), primary_key=True)
  version = Column(Integer, nullable=False)
  policy = Column(JSON, nullable=False)
  updated_by_user_id = Column(String(36), nullable=False)
