from datetime import datetime
from flask_login import UserMixin
from app import db

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
