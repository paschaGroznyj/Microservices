from datetime import datetime
from sqlalchemy import String, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass

class Chunk(Base):

    __tablename__ = 'chunk'

    id: Mapped[int] = mapped_column(primary_key=True)
    original_id: Mapped[str] = mapped_column(String(36))
    original_name: Mapped[str] = mapped_column(String(200))
    chunk_text: Mapped[str] = mapped_column(String(1000))
    embedding: Mapped[Vector] = mapped_column(Vector(1024))
    load_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    load_id: Mapped[str] = mapped_column(String(36))