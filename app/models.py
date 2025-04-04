from datetime import datetime
from flask_login import UserMixin
from . import db  # Import db from the app package (__init__.py)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<User {self.username}>'

class Inquiry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date_received = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    address = db.Column(db.Text)
    dob = db.Column(db.String(20))  # Using string for flexibility in date formats
    travel_start = db.Column(db.String(20))
    travel_end = db.Column(db.String(20))
    trip_cost = db.Column(db.Float)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    status = db.Column(db.String(20), default='Incomplete', 
                       nullable=False)  # Complete, Incomplete, Error
    raw_email_content = db.Column(db.Text)  # Optional, to store original email

    def __repr__(self):
        return f'<Inquiry {self.id}: {self.first_name} {self.last_name}>'

class Email(db.Model):
    __tablename__ = 'emails'

    # Core Email Fields from Graph API
    graph_id = db.Column(db.String, primary_key=True) # Use Graph API message ID as primary key
    subject = db.Column(db.String, nullable=True)
    sender_address = db.Column(db.String, nullable=True)
    sender_name = db.Column(db.String, nullable=True)
    received_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Processing Status Fields
    processed_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    processing_status = db.Column(db.String, default='pending') # e.g., pending, processed, error, needs_review
    processing_error = db.Column(db.Text, nullable=True) # Store error message if processing fails

    # Relationships
    extracted_data = db.relationship('ExtractedData', backref='email', uselist=False, cascade="all, delete-orphan")
    attachments = db.relationship('AttachmentMetadata', backref='email', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Email {self.graph_id} - {self.subject[:30]}>'

class ExtractedData(db.Model):
    __tablename__ = 'extracted_data'

    id = db.Column(db.Integer, primary_key=True)
    email_graph_id = db.Column(db.String, db.ForeignKey('emails.graph_id'), nullable=False, unique=True)

    # Store the extracted fields as a JSON blob
    data = db.Column(JSONB, nullable=True) # Use JSONB for PostgreSQL

    # Metadata about the extraction
    extraction_source = db.Column(db.String, nullable=True) # e.g., local, openai, combined
    validation_status = db.Column(db.String, nullable=True) # e.g., Complete, Incomplete
    missing_fields = db.Column(db.Text, nullable=True) # Store comma-separated missing fields if incomplete
    extracted_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    def __repr__(self):
        return f'<ExtractedData for Email {self.email_graph_id}>'

class AttachmentMetadata(db.Model):
    __tablename__ = 'attachment_metadata'

    graph_id = db.Column(db.String, primary_key=True) # Use Graph API attachment ID as primary key
    email_graph_id = db.Column(db.String, db.ForeignKey('emails.graph_id'), nullable=False)

    # Metadata from Graph API
    name = db.Column(db.String, nullable=True)
    content_type = db.Column(db.String, nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)

    # Optional: Link to stored content if downloading/storing attachments
    # storage_path = db.Column(db.String, nullable=True)

    added_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    def __repr__(self):
        return f'<Attachment {self.name} ({self.graph_id})>' 