from datetime import datetime
from flask_login import UserMixin, current_user
from . import db  # Import db from the app package (__init__.py)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<User {self.username}>'

class Inquiry(db.Model):
    __tablename__ = 'inquiries'

    id = db.Column(db.Integer, primary_key=True)
    primary_email_address = db.Column(db.String(120), nullable=False, index=True)
    status = db.Column(db.String(50), default='new', nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=func.now())

    emails = db.relationship('Email', backref='inquiry', lazy='dynamic')
    extracted_data = db.relationship('ExtractedData', backref='inquiry', uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Inquiry {self.id} for {self.primary_email_address}>'

class Email(db.Model):
    __tablename__ = 'emails'

    graph_id = db.Column(db.String, primary_key=True)
    subject = db.Column(db.String, nullable=True)
    sender_address = db.Column(db.String, nullable=True)
    sender_name = db.Column(db.String, nullable=True)
    received_at = db.Column(db.DateTime(timezone=True), nullable=True)
    processed_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    processing_status = db.Column(db.String, default='pending')
    processing_error = db.Column(db.Text, nullable=True)
    inquiry_id = db.Column(db.Integer, db.ForeignKey('inquiries.id'), nullable=True, index=True)
    attachments = db.relationship('AttachmentMetadata', backref='email', cascade="all, delete-orphan")
    intent = db.Column(db.String(50), nullable=True, index=True)

    def __repr__(self):
        return f'<Email {self.graph_id} - {self.subject[:30]}>'

class ExtractedData(db.Model):
    __tablename__ = 'extracted_data'

    id = db.Column(db.Integer, primary_key=True)
    inquiry_id = db.Column(db.Integer, db.ForeignKey('inquiries.id'), nullable=False, unique=True, index=True)

    data = db.Column(JSONB, nullable=True)
    extraction_source = db.Column(db.String, nullable=True)
    validation_status = db.Column(db.String, nullable=True)
    missing_fields = db.Column(db.Text, nullable=True)
    extracted_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=func.now())
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    updated_by_user = db.relationship('User', backref='edited_extracted_data')

    def __repr__(self):
        return f'<ExtractedData for Inquiry {self.inquiry_id}>'

class AttachmentMetadata(db.Model):
    __tablename__ = 'attachment_metadata'

    graph_id = db.Column(db.String, primary_key=True)
    email_graph_id = db.Column(db.String, db.ForeignKey('emails.graph_id'), nullable=False, index=True)

    name = db.Column(db.String, nullable=True)
    content_type = db.Column(db.String, nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)

    added_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f'<Attachment {self.name} ({self.graph_id})>' 