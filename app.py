import os
import logging
from datetime import datetime
from io import StringIO
import csv

from flask import (
    Flask, render_template, redirect, url_for, request, flash, 
    session, abort, jsonify, send_file
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import DeclarativeBase

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Setup SQLAlchemy with new API
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Create Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Configure database
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///booking_inquiries.db")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# Import models after initializing db to avoid circular imports
from models import User, Inquiry

# Create all database tables
with app.app_context():
    db.create_all()
    # Create default admin user if not exists
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            email='admin@example.com',
            password_hash=generate_password_hash('admin')
        )
        db.session.add(admin)
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Login unsuccessful. Please check username and password', 'danger')
            
    return render_template('login.html', title='Login')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Get filter parameters
    status_filter = request.args.get('status', '')
    search_query = request.args.get('search', '')
    
    # Get counts for dashboard stats
    total_count = Inquiry.query.count()
    complete_count = Inquiry.query.filter_by(status='Complete').count()
    incomplete_count = Inquiry.query.filter_by(status='Incomplete').count()
    error_count = Inquiry.query.filter_by(status='Error').count()
    
    return render_template(
        'dashboard.html', 
        title='Booking Inquiry Dashboard',
        status_filter=status_filter,
        search_query=search_query,
        total_count=total_count,
        complete_count=complete_count,
        incomplete_count=incomplete_count,
        error_count=error_count
    )

@app.route('/api/inquiries')
@login_required
def get_inquiries():
    # Get DataTables parameters
    draw = request.args.get('draw', type=int)
    start = request.args.get('start', type=int)
    length = request.args.get('length', type=int)
    search_value = request.args.get('search[value]', '')
    order_column_idx = request.args.get('order[0][column]', type=int)
    order_dir = request.args.get('order[0][dir]')
    status_filter = request.args.get('status_filter', '')
    
    # Column list for ordering
    columns = ['id', 'date_received', 'first_name', 'last_name', 'email', 'phone', 
               'travel_start', 'travel_end', 'trip_cost', 'status']
    
    # Query base
    query = Inquiry.query
    
    # Apply filters
    if status_filter and status_filter != 'All':
        query = query.filter(Inquiry.status == status_filter)
    
    # Apply search
    if search_value:
        search_term = f'%{search_value}%'
        query = query.filter(
            (Inquiry.first_name.like(search_term)) |
            (Inquiry.last_name.like(search_term)) |
            (Inquiry.email.like(search_term)) |
            (Inquiry.phone.like(search_term)) |
            (Inquiry.status.like(search_term))
        )
    
    # Count total records
    total_records = query.count()
    
    # Apply ordering
    if order_column_idx is not None:
        column_name = columns[order_column_idx]
        column = getattr(Inquiry, column_name)
        
        if order_dir == 'desc':
            query = query.order_by(column.desc())
        else:
            query = query.order_by(column.asc())
    else:
        # Default order by date received (newest first)
        query = query.order_by(Inquiry.date_received.desc())
    
    # Apply pagination
    inquiries = query.offset(start).limit(length).all()
    
    # Prepare data for DataTables
    data = []
    for inquiry in inquiries:
        # Format dates for display
        travel_start = inquiry.travel_start if inquiry.travel_start else ''
        travel_end = inquiry.travel_end if inquiry.travel_end else ''
        
        # Create full name
        full_name = f"{inquiry.first_name} {inquiry.last_name}".strip()
        
        # Format date received
        date_received = inquiry.date_received.strftime('%Y-%m-%d %H:%M')
        
        data.append({
            'id': inquiry.id,
            'date_received': date_received,
            'client_name': full_name,
            'email': inquiry.email,
            'phone': inquiry.phone,
            'travel_dates': f"{travel_start} to {travel_end}" if travel_start and travel_end else "",
            'trip_cost': f"${inquiry.trip_cost:.2f}" if inquiry.trip_cost else "",
            'status': inquiry.status,
            'actions': f'<a href="/inquiry/{inquiry.id}" class="btn btn-sm btn-info">View/Edit</a>'
        })
    
    # Prepare response
    response = {
        'draw': draw,
        'recordsTotal': total_records,
        'recordsFiltered': total_records,
        'data': data
    }
    
    return jsonify(response)

@app.route('/inquiry/<int:inquiry_id>', methods=['GET'])
@login_required
def inquiry_detail(inquiry_id):
    inquiry = Inquiry.query.get_or_404(inquiry_id)
    return render_template('inquiry_detail.html', inquiry=inquiry, title='Inquiry Details')

@app.route('/update/<int:inquiry_id>', methods=['POST'])
@login_required
def update_inquiry(inquiry_id):
    inquiry = Inquiry.query.get_or_404(inquiry_id)
    
    # Update inquiry with form data
    inquiry.first_name = request.form.get('first_name', '')
    inquiry.last_name = request.form.get('last_name', '')
    inquiry.address = request.form.get('address', '')
    inquiry.dob = request.form.get('dob', '')
    inquiry.travel_start = request.form.get('travel_start', '')
    inquiry.travel_end = request.form.get('travel_end', '')
    
    # Handle numeric field with proper error handling
    try:
        trip_cost = request.form.get('trip_cost', '')
        inquiry.trip_cost = float(trip_cost) if trip_cost else None
    except ValueError:
        flash('Invalid trip cost value. Please enter a valid number.', 'danger')
        return redirect(url_for('inquiry_detail', inquiry_id=inquiry_id))
    
    inquiry.email = request.form.get('email', '')
    inquiry.phone = request.form.get('phone', '')
    inquiry.status = request.form.get('status', 'Incomplete')
    
    try:
        db.session.commit()
        flash('Inquiry updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating inquiry: {str(e)}', 'danger')
        logging.error(f"Error updating inquiry: {str(e)}")
    
    return redirect(url_for('inquiry_detail', inquiry_id=inquiry_id))

@app.route('/export')
@login_required
def export_csv():
    # Get all inquiries
    inquiries = Inquiry.query.all()
    
    # Create CSV in memory
    si = StringIO()
    writer = csv.writer(si)
    
    # Write header
    writer.writerow([
        'ID', 'Date Received', 'First Name', 'Last Name', 'Address', 'Date of Birth',
        'Travel Start', 'Travel End', 'Trip Cost', 'Email', 'Phone', 'Status'
    ])
    
    # Write data
    for inquiry in inquiries:
        writer.writerow([
            inquiry.id,
            inquiry.date_received.strftime('%Y-%m-%d %H:%M:%S'),
            inquiry.first_name,
            inquiry.last_name,
            inquiry.address,
            inquiry.dob,
            inquiry.travel_start,
            inquiry.travel_end,
            inquiry.trip_cost,
            inquiry.email,
            inquiry.phone,
            inquiry.status
        ])
    
    # Prepare response
    output = si.getvalue()
    si.close()
    
    return send_file(
        StringIO(output),
        mimetype='text/csv',
        download_name=f'booking_inquiries_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv',
        as_attachment=True
    )

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500
