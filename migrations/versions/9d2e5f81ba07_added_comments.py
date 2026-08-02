"""Added comments

Revision ID: 9d2e5f81ba07
Revises: c4a91be7d503
Create Date: 2026-08-02 11:42:18.905331

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d2e5f81ba07'
down_revision: Union[str, Sequence[str], None] = 'c4a91be7d503'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'comment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('dish_id', sa.Integer(), nullable=False),
        # Nullable: a deleted author leaves the thread readable.
        sa.Column('author_id', sa.Integer(), nullable=True),
        # Self-reference. NULL means a top-level comment.
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['dish_id'], ['dish.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['author_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['parent_id'], ['comment.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Both columns are filtered on when listing a dish's top-level comments.
    op.create_index(op.f('ix_comment_dish_id'), 'comment', ['dish_id'])
    op.create_index(op.f('ix_comment_parent_id'), 'comment', ['parent_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_comment_parent_id'), table_name='comment')
    op.drop_index(op.f('ix_comment_dish_id'), table_name='comment')
    op.drop_table('comment')
