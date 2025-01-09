"""add user cost column
Revision ID: add_user_cost
Revises: 001
Create Date: 2024-12-23
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_user_cost'
down_revision = '001'  
depends_on = None

def upgrade() -> None:
    op.add_column('users', sa.Column('user_cost', sa.Float(), nullable=False, server_default='0.0'))

def downgrade() -> None:
    op.drop_column('users', 'user_cost')