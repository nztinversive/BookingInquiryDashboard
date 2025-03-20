from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, SubmitField, SelectField,
    TextAreaField, FloatField, DateField
)
from wtforms.validators import DataRequired, Email, Optional, Length, ValidationError
import re

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class InquiryForm(FlaskForm):
    first_name = StringField('First Name', validators=[Length(max=100)])
    last_name = StringField('Last Name', validators=[Length(max=100)])
    address = TextAreaField('Home Address')
    dob = StringField('Date of Birth')
    travel_start = StringField('Travel Start Date')
    travel_end = StringField('Travel End Date')
    trip_cost = FloatField('Trip Cost', validators=[Optional()])
    email = StringField('Email', validators=[Optional(), Email()])
    phone = StringField('Phone Number')
    status = SelectField('Status', choices=[
        ('Complete', 'Complete'),
        ('Incomplete', 'Incomplete'),
        ('Error', 'Error')
    ])
    submit = SubmitField('Update Inquiry')

    def validate_phone(self, phone):
        # Allow empty phone numbers
        if not phone.data:
            return
        
        # Simple validation for phone numbers
        phone_pattern = re.compile(r'^\+?[0-9\-\(\)\s]{7,20}$')
        if not phone_pattern.match(phone.data):
            raise ValidationError('Please enter a valid phone number')
