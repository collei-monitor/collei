"""add_billing_cost_code_and_expiry

Revision ID: ef5a6b7c8d9e
Revises: df4e5f6a7b9d
Create Date: 2026-03-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef5a6b7c8d9e'
down_revision: Union[str, None] = 'df4e5f6a7b9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('server_billing_rules', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('billing_cycle_cost_code', sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column('expiry_date', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('server_billing_rules', schema=None) as batch_op:
        batch_op.drop_column('expiry_date')
        batch_op.drop_column('billing_cycle_cost_code')
