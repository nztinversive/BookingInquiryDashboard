"""Add WhatsAppMessage model

Revision ID: b27d9abcf23
Revises: 4e599d1d708d
Create Date: 2024-06-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b27d9abcf23'
down_revision = '4e599d1d708d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'whatsapp_messages',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('inquiry_id', sa.Integer(), sa.ForeignKey('inquiries.id'), nullable=True, index=True),
        sa.Column('wa_chat_id', sa.String(), nullable=False, index=True),
        sa.Column('sender_number', sa.String(), nullable=True),
        sa.Column('from_me', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('message_type', sa.String(length=50), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('media_url', sa.String(), nullable=True),
        sa.Column('media_mime_type', sa.String(), nullable=True),
        sa.Column('media_caption', sa.Text(), nullable=True),
        sa.Column('media_filename', sa.String(), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('location_description', sa.String(), nullable=True),
        sa.Column('wa_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now())
    )
    op.create_index('ix_whatsapp_messages_inquiry_id', 'whatsapp_messages', ['inquiry_id'])
    op.create_index('ix_whatsapp_messages_wa_chat_id', 'whatsapp_messages', ['wa_chat_id'])


def downgrade():
    op.drop_index('ix_whatsapp_messages_wa_chat_id', table_name='whatsapp_messages')
    op.drop_index('ix_whatsapp_messages_inquiry_id', table_name='whatsapp_messages')
    op.drop_table('whatsapp_messages') 