"""Add node credentials and durable outbound command queue."""

import sqlalchemy as sa
from alembic import op

revision = "0003_node_pull_control"
down_revision = "0002_nodes_interfaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "node_credentials" not in tables:
        op.create_table(
            "node_credentials",
            sa.Column("node_id", sa.String(40), sa.ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "ix_node_credentials_tenant_id" not in {
            item["name"] for item in sa.inspect(op.get_bind()).get_indexes("node_credentials")
    }:
        op.create_index("ix_node_credentials_tenant_id", "node_credentials", ["tenant_id"])

    if "node_commands" not in tables:
        op.create_table(
            "node_commands",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("node_id", sa.String(40), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("operation", sa.String(40), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
    command_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("node_commands")}
    if "ix_node_commands_node_id" not in command_indexes:
        op.create_index("ix_node_commands_node_id", "node_commands", ["node_id"])
    if "ix_node_commands_poll" not in command_indexes:
        op.create_index("ix_node_commands_poll", "node_commands", ["node_id", "status", "created_at"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "node_commands" in tables:
        indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("node_commands")}
        for name in ("ix_node_commands_poll", "ix_node_commands_node_id"):
            if name in indexes:
                op.drop_index(name, table_name="node_commands")
        op.drop_table("node_commands")
    if "node_credentials" in tables:
        indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("node_credentials")}
        if "ix_node_credentials_tenant_id" in indexes:
            op.drop_index("ix_node_credentials_tenant_id", table_name="node_credentials")
        op.drop_table("node_credentials")
