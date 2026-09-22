"""Add append-only account situation notes."""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"


def upgrade():
    op.create_table(
        "account_notes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column(
            "author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 200",
            name="account_notes_content_length",
        ),
    )
    op.create_index(
        "ix_account_notes_account_id_id", "account_notes", ["account_id", "id"]
    )


def downgrade():
    # Never discard history when an operator rolls application code back.
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM account_notes)")):
        raise RuntimeError(
            "Account note history exists; retain this schema when rolling back application code"
        )
    op.drop_table("account_notes")
