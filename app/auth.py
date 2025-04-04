from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash # For checking passwords

# Import User model and db object
from .models import User
from . import db

auth_bp = Blueprint('auth', __name__,
                    template_folder='templates',
                    static_folder='static')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard')) # Redirect if already logged in

    if request.method == 'POST':
        # --- Placeholder Login Logic --- 
        # Replace with your actual user lookup and password checking
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False

        # --- Actual Login Logic --- 
        # Find user by username
        user = User.query.filter_by(username=username).first()

        # Check if user exists and password hash matches
        if not user or not check_password_hash(user.password_hash, password):
            flash('Please check your login details and try again.')
            return redirect(url_for('auth.login'))
        
        # If login is successful:
        login_user(user, remember=remember)
        # Redirect to the dashboard or the page the user was trying to access
        next_page = request.args.get('next')
        return redirect(next_page or url_for('main.dashboard'))
        # --- End Actual Login Logic --- 

    # Render login page for GET request
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    """Handle user logout."""
    logout_user()
    return redirect(url_for('auth.login'))

# Add registration route if needed
# @auth_bp.route('/register', methods=['GET', 'POST'])
# def register():
#    ... 