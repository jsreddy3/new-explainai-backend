"""add google auth

Revision ID: 001
Revises: 
Create Date: 2024-12-12 13:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to users table
    op.add_column('users', sa.Column('google_id', sa.String(), nullable=True))
    op.add_column('users', sa.Column('last_login', sa.DateTime(), nullable=True))
    op.create_unique_constraint('uq_users_google_id', 'users', ['google_id'])
    
    # Add owner_id to documents table
    op.add_column('documents', sa.Column('owner_id', sa.String(), nullable=True))
    op.create_foreign_key(
        'fk_documents_owner_id_users',
        'documents', 'users',
        ['owner_id'], ['id']
    )


def downgrade() -> None:
    # Remove foreign key and owner_id from documents
    op.drop_constraint('fk_documents_owner_id_users', 'documents', type_='foreignkey')
    op.drop_column('documents', 'owner_id')
    
    # Remove new columns from users
    op.drop_constraint('uq_users_google_id', 'users', type_='unique')
    op.drop_column('users', 'google_id')
    op.drop_column('users', 'last_login')
