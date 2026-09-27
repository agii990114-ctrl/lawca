"""library compact final draft

Revision ID: f4a8291f9103
Revises: db55ccc491d2
Create Date: 2026-09-27 17:36:05.310335

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a8291f9103'
down_revision: Union[str, Sequence[str], None] = 'db55ccc491d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('drafts', sa.Column('final_file_id', sa.UUID(), nullable=True))
    op.add_column('drafts', sa.Column('final_uploaded_by', sa.String(length=50), nullable=True))
    op.add_column('drafts', sa.Column('final_uploaded_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('drafts_final_file_id_fkey', 'drafts', 'files', ['final_file_id'], ['id'])
    op.add_column('library_chunks', sa.Column('compact', sa.Text(), sa.Computed("regexp_replace(text, '\\s+', '', 'g')", persisted=True), nullable=False))
    op.drop_index(op.f('ix_library_chunks_text_trgm'), table_name='library_chunks', postgresql_ops={'text': 'gin_trgm_ops'}, postgresql_using='gin')
    op.create_index('ix_library_chunks_compact_trgm', 'library_chunks', ['compact'], unique=False, postgresql_using='gin', postgresql_ops={'compact': 'gin_trgm_ops'})


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_library_chunks_compact_trgm', table_name='library_chunks', postgresql_using='gin', postgresql_ops={'compact': 'gin_trgm_ops'})
    op.create_index(op.f('ix_library_chunks_text_trgm'), 'library_chunks', ['text'], unique=False, postgresql_ops={'text': 'gin_trgm_ops'}, postgresql_using='gin')
    op.drop_column('library_chunks', 'compact')
    op.drop_constraint('drafts_final_file_id_fkey', 'drafts', type_='foreignkey')
    op.drop_column('drafts', 'final_uploaded_at')
    op.drop_column('drafts', 'final_uploaded_by')
    op.drop_column('drafts', 'final_file_id')
