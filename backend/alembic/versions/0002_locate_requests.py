"""Add on-demand device locate requests and diagnostics.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    device_columns = {column["name"] for column in sa.inspect(bind).get_columns("elder_devices")}
    new_columns = (
        sa.Column("last_command_poll_at", sa.DateTime(timezone=True)),
        sa.Column("background_location_granted", sa.Boolean()),
        sa.Column("battery_optimization_ignored", sa.Boolean()),
        sa.Column("location_services_enabled", sa.Boolean()),
        sa.Column("last_service_error", sa.String(length=120)),
    )
    for column in new_columns:
        if column.name not in device_columns:
            op.add_column("elder_devices", column)

    if not sa.inspect(bind).has_table("device_locate_requests"):
        op.create_table(
            "device_locate_requests",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("family_id", sa.Uuid(), nullable=False),
            sa.Column("elder_id", sa.Uuid(), nullable=False),
            sa.Column("device_id", sa.Uuid(), nullable=False),
            sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
            sa.Column("state", sa.String(length=20), nullable=False),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("failed_at", sa.DateTime(timezone=True)),
            sa.Column("failure_code", sa.String(length=40)),
            sa.Column("result_track_point_id", sa.Uuid()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["family_id"], ["families.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["elder_id"], ["elders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["device_id"], ["elder_devices.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["requested_by_user_id"], ["users.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["result_track_point_id"], ["track_points.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("device_locate_requests")
    }
    if "ix_locate_requests_device_state" not in existing_indexes:
        op.create_index(
            "ix_locate_requests_device_state", "device_locate_requests", ["device_id", "state"]
        )
    if "ix_locate_requests_elder_requested" not in existing_indexes:
        op.create_index(
            "ix_locate_requests_elder_requested",
            "device_locate_requests",
            ["elder_id", "requested_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_locate_requests_elder_requested", table_name="device_locate_requests")
    op.drop_index("ix_locate_requests_device_state", table_name="device_locate_requests")
    op.drop_table("device_locate_requests")
    op.drop_column("elder_devices", "last_service_error")
    op.drop_column("elder_devices", "location_services_enabled")
    op.drop_column("elder_devices", "battery_optimization_ignored")
    op.drop_column("elder_devices", "background_location_granted")
    op.drop_column("elder_devices", "last_command_poll_at")
