"""Added dish author

Revision ID: c4a91be7d503
Revises: f6153ccfd2fc
Create Date: 2026-08-02 10:15:03.482917

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a91be7d503'
down_revision: Union[str, Sequence[str], None] = 'f6153ccfd2fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable: dishes created before authorship existed have no author, and
    # backfilling them would mean inventing an owner.
    op.add_column('dish', sa.Column('author_id', sa.Integer(), nullable=True))
    op.create_foreign_key('dish_author_id_fkey', 'dish', 'user', ['author_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('dish_author_id_fkey', 'dish', type_='foreignkey')
    op.drop_column('dish', 'author_id')
