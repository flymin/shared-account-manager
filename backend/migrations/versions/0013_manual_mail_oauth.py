"""Remember the redirect URI for manual OAuth completion."""

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"


def upgrade():
    op.add_column(
        "mail_oauth_states",
        sa.Column("redirect_uri", sa.String(512), nullable=True),
    )


def downgrade():
    op.drop_column("mail_oauth_states", "redirect_uri")
