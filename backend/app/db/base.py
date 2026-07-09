"""SQLAlchemy declarative base. All ORM models inherit from this."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project-wide declarative base."""
    pass
