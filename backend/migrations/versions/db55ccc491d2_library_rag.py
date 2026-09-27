"""library rag

Revision ID: db55ccc491d2
Revises: 0961078d498d
Create Date: 2026-09-27 16:34:49.140680

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from lawca.db.models import Vector

# revision identifiers, used by Alembic.
revision: str = 'db55ccc491d2'
down_revision: Union[str, Sequence[str], None] = '0961078d498d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # pgvector(의미 검색)와 pg_trgm(한국어 부분 일치). docker-compose의 pgvector 이미지에 들어 있다.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table('library_docs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('file_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=True),
    sa.Column('document_id', sa.UUID(), nullable=True),
    sa.Column('draft_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.String(length=50), nullable=False),
    sa.Column('chunk_count', sa.Integer(), nullable=False),
    sa.Column('embedded', sa.Boolean(), nullable=False),
    sa.Column('embedding_model', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['draft_id'], ['drafts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id'),
    sa.UniqueConstraint('draft_id')
    )
    op.create_index(op.f('ix_library_docs_case_id'), 'library_docs', ['case_id'], unique=False)
    op.create_index(op.f('ix_library_docs_kind'), 'library_docs', ['kind'], unique=False)
    op.create_table('library_chunks',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('doc_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('embedding', Vector(1024), nullable=True),
    sa.ForeignKeyConstraint(['doc_id'], ['library_docs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_library_chunks_doc_id'), 'library_chunks', ['doc_id'], unique=False)
    op.create_index('ix_library_chunks_text_trgm', 'library_chunks', ['text'], unique=False,
                    postgresql_using='gin', postgresql_ops={'text': 'gin_trgm_ops'})
    op.create_index('ix_library_chunks_embedding', 'library_chunks', ['embedding'], unique=False,
                    postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_library_chunks_embedding', table_name='library_chunks')
    op.drop_index('ix_library_chunks_text_trgm', table_name='library_chunks')
    op.drop_index(op.f('ix_library_chunks_doc_id'), table_name='library_chunks')
    op.drop_table('library_chunks')
    op.drop_index(op.f('ix_library_docs_kind'), table_name='library_docs')
    op.drop_index(op.f('ix_library_docs_case_id'), table_name='library_docs')
    op.drop_table('library_docs')
