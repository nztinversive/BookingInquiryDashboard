"""Add intent column to Email

Revision ID: 4e599d1d708d
Revises: a0538771fe88
Create Date: 2025-04-09 13:38:48.544209

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4e599d1d708d'
down_revision = 'a0538771fe88'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.add_column(sa.Column('intent', sa.String(length=50), nullable=True))
        batch_op.create_index(batch_op.f('ix_emails_intent'), ['intent'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_emails_intent'))
        batch_op.drop_column('intent')

    # ### end Alembic commands ###
