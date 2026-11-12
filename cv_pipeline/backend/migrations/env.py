"""Alembic runs against DATABASE_URL, the same setting the app uses."""
from alembic import context

from app.db.models import Base
from app.db.session import get_engine

with get_engine().connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
