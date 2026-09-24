"""Add node registry and interface attachment."""

import sqlalchemy as sa
from alembic import op

revision = "0002_nodes_interfaces"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "nodes" not in tables:
        op.create_table(
            "nodes",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("tenant_id", sa.String(80), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("country", sa.String(2), nullable=False),
            sa.Column("agent_url", sa.String(500), nullable=False),
            sa.Column("agent_secret_ciphertext", sa.Text(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("healthy", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_nodes_tenant_id", "nodes", ["tenant_id"])
    cols = {column["name"] for column in inspector.get_columns("servers")}
    if "node_id" not in cols:
        op.add_column("servers", sa.Column("node_id", sa.String(40),
                                             sa.ForeignKey("nodes.id"), nullable=True))
        op.create_index("ix_servers_node_id", "servers", ["node_id"])
    if "interface_name" not in cols:
        op.add_column("servers", sa.Column("interface_name", sa.String(15),
                                             nullable=False, server_default="wg0"))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("servers")}
    if "uq_servers_node_interface" not in indexes:
        op.create_index("uq_servers_node_interface", "servers", ["node_id", "interface_name"],
                        unique=True)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    cols = {column["name"] for column in inspector.get_columns("servers")}
    if "interface_name" in cols:
        indexes = {index["name"] for index in inspector.get_indexes("servers")}
        if "uq_servers_node_interface" in indexes:
            op.drop_index("uq_servers_node_interface", table_name="servers")
        op.drop_column("servers", "interface_name")
    if "node_id" in cols:
        op.drop_index("ix_servers_node_id", table_name="servers")
        op.drop_column("servers", "node_id")
    if "nodes" in set(inspector.get_table_names()):
        op.drop_index("ix_nodes_tenant_id", table_name="nodes")
        op.drop_table("nodes")
