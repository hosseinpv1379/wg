"""Keep peer traffic totals monotonic across WireGuard counter resets."""

import sqlalchemy as sa
from alembic import op

revision = "0004_persistent_peer_usage"
down_revision = "0003_node_pull_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    server_columns = {column["name"] for column in sa.inspect(bind).get_columns("servers")}
    peer_columns = {column["name"] for column in sa.inspect(bind).get_columns("peers")}
    if "interface_generation" not in server_columns:
        op.add_column("servers", sa.Column(
            "interface_generation", sa.String(100), nullable=False, server_default=""))
    if "last_rx_bytes" not in peer_columns:
        op.add_column("peers", sa.Column(
            "last_rx_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    if "last_tx_bytes" not in peer_columns:
        op.add_column("peers", sa.Column(
            "last_tx_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    op.execute("UPDATE peers SET last_rx_bytes = rx_bytes, last_tx_bytes = tx_bytes")


def downgrade() -> None:
    bind = op.get_bind()
    peer_columns = {column["name"] for column in sa.inspect(bind).get_columns("peers")}
    server_columns = {column["name"] for column in sa.inspect(bind).get_columns("servers")}
    if "last_tx_bytes" in peer_columns:
        op.drop_column("peers", "last_tx_bytes")
    if "last_rx_bytes" in peer_columns:
        op.drop_column("peers", "last_rx_bytes")
    if "interface_generation" in server_columns:
        op.drop_column("servers", "interface_generation")
