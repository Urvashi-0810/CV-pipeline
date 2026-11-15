from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Client(Base):
    """A company that sends us demand inquiries ("Client Pool")."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    contacts: Mapped[list["ClientContact"]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
        order_by="ClientContact.sort_order",
        lazy="selectin",
    )

    __table_args__ = (
        # One record per company, whatever the letter case or surrounding spaces.
        Index("uq_clients_name_lower", func.lower(func.btrim(name)), unique=True),
        CheckConstraint("length(btrim(name)) between 1 and 200", name="name_length"),
        CheckConstraint("email is null or length(email) <= 254", name="email_length"),
    )


class ClientContact(Base):
    """A person at the client we deal with. A client can have any number of them."""

    __tablename__ = "client_contacts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    client: Mapped[Client] = relationship(back_populates="contacts")

    __table_args__ = (
        CheckConstraint("length(btrim(name)) between 1 and 100", name="name_length"),
        CheckConstraint("phone is null or length(phone) <= 30", name="phone_length"),
        CheckConstraint("email is null or length(email) <= 254", name="email_length"),
    )
