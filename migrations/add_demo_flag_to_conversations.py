"""add demo flag to conversations

Revision ID: add_demo_flag_to_conversations
Revises: 
Create Date: 2024-12-15 09:28:19.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_demo_flag_to_conversations'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add is_demo column to conversations table
    op.add_column('conversations', sa.Column('is_demo', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    # Remove is_demo column from conversations table
    op.drop_column('conversations', 'is_demo')
