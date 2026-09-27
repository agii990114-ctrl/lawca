"""document corrections

Revision ID: 0961078d498d
Revises: b051aa694914
Create Date: 2026-09-27 15:56:18.415192

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0961078d498d'
down_revision: Union[str, Sequence[str], None] = 'b051aa694914'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 기존 문서는 고친 값이 없다({})
    op.add_column('documents', sa.Column('corrections', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.alter_column('documents', 'corrections', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('documents', 'corrections')
