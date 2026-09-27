"""evidence and brief summary

Revision ID: f8d5582c8f9d
Revises: f4a8291f9103
Create Date: 2026-09-27 18:25:37.978547

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f8d5582c8f9d'
down_revision: Union[str, Sequence[str], None] = 'f4a8291f9103'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('evidence',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('side', sa.String(length=2), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('file_id', sa.UUID(), nullable=True),
    sa.Column('source_document_id', sa.UUID(), nullable=True),
    sa.Column('submitted_on', sa.Date(), nullable=True),
    sa.Column('created_by', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], ),
    sa.ForeignKeyConstraint(['source_document_id'], ['documents.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('case_id', 'side', 'number')
    )
    op.create_index(op.f('ix_evidence_case_id'), 'evidence', ['case_id'], unique=False)
    op.add_column('documents', sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('documents', 'summary')
    op.drop_index(op.f('ix_evidence_case_id'), table_name='evidence')
    op.drop_table('evidence')
