"""Preserve the coordinate reference reported by the device."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    columns = sa.inspect(op.get_bind()).get_columns("track_points")
    if "coordinate_system" not in {column["name"] for column in columns}:
        op.add_column(
            "track_points",
            sa.Column("coordinate_system", sa.String(8), nullable=False, server_default="GCJ02"),
        )


def downgrade():
    op.drop_column("track_points", "coordinate_system")
