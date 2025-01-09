"""add s3_file_path column

Revision ID: add_s3_file_path_20250109
Revises: add_demo_flag_to_conversations
Create Date: 2025-01-09 15:47:53.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = 'add_s3_file_path_20250109'
down_revision = 'add_demo_flag_to_conversations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add s3_file_path column to documents table
    # Using batch_alter_table for SQLite compatibility, but it works fine with PostgreSQL too
    with op.batch_alter_table('documents') as batch_op:
        batch_op.add_column(sa.Column('s3_file_path', sa.String(), nullable=True))


def downgrade() -> None:
    # Remove s3_file_path column from documents table
    with op.batch_alter_table('documents') as batch_op:
        batch_op.drop_column('s3_file_path')
