"""calendar events

Revision ID: b051aa694914
Revises: 0c9608b0088e
Create Date: 2026-09-27 14:59:50.355854

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b051aa694914'
down_revision: Union[str, Sequence[str], None] = '0c9608b0088e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('at', sa.Time(), nullable=True),
    sa.Column('location', sa.String(length=200), nullable=True),
    sa.Column('memo', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('visibility', sa.String(length=16), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=True),
    sa.Column('document_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.String(length=50), nullable=False),
    sa.Column('confirmed_by', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_events_case_id'), 'events', ['case_id'], unique=False)
    op.create_index(op.f('ix_events_day'), 'events', ['day'], unique=False)
    op.create_index(op.f('ix_events_document_id'), 'events', ['document_id'], unique=False)
    op.create_index(op.f('ix_events_status'), 'events', ['status'], unique=False)
    op.add_column('documents', sa.Column('pending_dismissed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('calendar_token_hash', sa.String(length=64), nullable=True))
    op.create_unique_constraint('users_calendar_token_hash_key', 'users', ['calendar_token_hash'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('users_calendar_token_hash_key', 'users', type_='unique')
    op.drop_column('users', 'calendar_token_hash')
    op.drop_column('documents', 'pending_dismissed_at')
    op.drop_index(op.f('ix_events_status'), table_name='events')
    op.drop_index(op.f('ix_events_document_id'), table_name='events')
    op.drop_index(op.f('ix_events_day'), table_name='events')
    op.drop_index(op.f('ix_events_case_id'), table_name='events')
    op.drop_table('events')
