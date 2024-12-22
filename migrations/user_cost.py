"""add user cost column
Revision ID: add_user_cost_column
Revises: add_demo_flag_to_conversations
Create Date: 2024-12-23 09:28:19.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_user_cost_column'
down_revision = 'add_demo_flag_to_conversations'  # Reference the previous migration
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Add user_cost column to users table
    op.add_column('users', sa.Column('user_cost', sa.Float(), nullable=False, server_default='0.0'))

def downgrade() -> None:
    # Remove user_cost column from users table
    op.drop_column('users', 'user_cost')