"""users sessions draft review

Revision ID: 0c9608b0088e
Revises: 87871946d48a
Create Date: 2026-09-27 14:33:07.108037

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0c9608b0088e'
down_revision: Union[str, Sequence[str], None] = '87871946d48a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_users_username_active', 'users', ['username'], unique=True, postgresql_where='deleted_at IS NULL')
    op.create_table('user_sessions',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False)
    # 기존 대화는 주인이 없어(user_id NULL) 아무에게도 보이지 않는다.
    op.add_column('conversations', sa.Column('user_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    op.create_foreign_key('conversations_user_id_fkey', 'conversations', 'users', ['user_id'], ['id'])
    op.add_column('drafts', sa.Column('created_by', sa.String(length=50), nullable=True))
    op.add_column('drafts', sa.Column('reviewed_by', sa.String(length=50), nullable=True))
    op.add_column('drafts', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('drafts', 'reviewed_at')
    op.drop_column('drafts', 'reviewed_by')
    op.drop_column('drafts', 'created_by')
    op.drop_constraint('conversations_user_id_fkey', 'conversations', type_='foreignkey')
    op.drop_index(op.f('ix_conversations_user_id'), table_name='conversations')
    op.drop_column('conversations', 'user_id')
    op.drop_index(op.f('ix_user_sessions_user_id'), table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_index('uq_users_username_active', table_name='users', postgresql_where='deleted_at IS NULL')
    op.drop_table('users')
