# Standard library imports
import os
import logging
import atexit
from datetime import datetime, timezone, timedelta # Added datetime
import json # Added json

# Third-party imports
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate # Added Flask-Migrate
import click # Added click for CLI commands
from flask.cli import with_appcontext # Added for CLI context

# --- Configure Logging ---
# Set level to DEBUG to capture detailed filter logs
logging.basicConfig(level=logging.DEBUG, 
                    format='%(asctime)s - %(levelname)s - %(name)s - %(threadName)s - %(message)s')

# --- Initialize Extensions ---
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login' # Specify the login view for redirection
migrate = Migrate() # Initialize Migrate

# --- Application Factory Function ---
def create_app():
    """Create and configure an instance of the Flask application."""
    # Explicitly set static folder relative to the 'app' directory
    app = Flask(__name__, 
                instance_relative_config=False,
                static_folder='../static', # Go up one level from 'app' directory
                static_url_path='/static') # Default, but set explicitly

    # --- Configuration ---
    # Load default config or config from environment variables
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_secret_key') # Use env var or default
    DATABASE_URL = os.environ.get('DATABASE_URL')

    if DATABASE_URL:
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True}
        logging.info("Database configured using DATABASE_URL.")
    else:
        logging.error("DATABASE_URL secret not found! Database features may be disabled.")
        # Consider exiting or using a fallback like SQLite for development
        # app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///default.db'

    # --- Initialize Extensions with App ---
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db) # Initialize Migrate with app and db

    # --- Application Context ---
    with app.app_context():
        # --- Import Blueprints ---
        from . import routes  # Import main application routes
        from .auth import auth_bp # Import authentication blueprint
        # Import other blueprints as needed

        # --- Register Blueprints ---
        app.register_blueprint(routes.main_bp)
        app.register_blueprint(auth_bp, url_prefix='/auth')
        # Register other blueprints

        # --- Initialize Database ---
        logging.info("Initializing database tables within app context...")
        try:
            db.create_all() # Create tables based on models imported by blueprints/routes
            logging.info("Database tables checked/created.")
        except Exception as e:
            logging.error(f"Error during database initialization: {e}", exc_info=True)

        # --- Import Models (ensure they are known to SQLAlchemy before create_all) ---
        # Typically models are imported in the modules where they are used (e.g., routes, auth)
        # If models are defined separately, ensure they are imported somewhere before db.create_all() is called.
        from . import models # Make sure models are imported so CLI command knows them

        # --- Configure Login Manager ---
        from .models import User # Import User model for user_loader
        @login_manager.user_loader
        def load_user(user_id):
            # Return user object from the user ID stored in the session
            return User.query.get(int(user_id))

        # --- Start Background Tasks (AFTER app is fully configured) ---
        # Check MS365 config before starting poller
        polling_started = False
        try:
            from ms_graph_service import get_ms365_config
            get_ms365_config() # This will raise ValueError if secrets are missing
            logging.info("MS365 configuration validated.")

            # Start the background polling thread
            from .background_tasks import start_background_polling, shutdown_background_polling
            start_background_polling(app)
            polling_started = True
            # Register shutdown hook for graceful termination
            atexit.register(shutdown_background_polling)
            logging.info("Registered background task shutdown hook.")

        except ValueError as config_err:
            logging.error(f"Halting background task startup due to config error: {config_err}")
        except Exception as startup_err:
            logging.error(f"Error during background task startup configuration check: {startup_err}", exc_info=True)

        if not polling_started:
            logging.warning("Background email polling thread WAS NOT started due to errors.")

        # --- Register CLI Commands ---
        register_cli_commands(app)

        # --- Return App Instance ---
        logging.info("Flask app created successfully.")
        return app

# --- CLI Command Definitions ---
def register_cli_commands(app):
    @app.cli.command('seed-sample')
    @with_appcontext
    def seed_sample_inquiry():
        """Creates a sample Inquiry, Email, and ExtractedData for demo purposes."""
        from .models import Inquiry, Email, ExtractedData # Import models inside command

        # Sample Data
        sample_email_address = "test.customer@example.com"
        sample_graph_id = "sample-email-1"
        sample_data = {
            "first_name": "Test",
            "last_name": "Customer",
            "travel_start_date": "2024-08-01",
            "travel_end_date": "2024-08-10",
            "trip_cost": "3150.75",
            "destination": "Paris",
            "number_of_travelers": "2"
        }

        click.echo("Starting sample data seeding...")
        try:
            # Check if inquiry already exists
            existing_inquiry = Inquiry.query.filter_by(primary_email_address=sample_email_address).first()
            if existing_inquiry:
                click.echo(f"Inquiry for {sample_email_address} already exists (ID: {existing_inquiry.id}). Skipping.")
                return

            # 1. Create Inquiry
            click.echo(f"Creating Inquiry for {sample_email_address}...")
            inquiry = Inquiry(
                primary_email_address=sample_email_address,
                status="Complete" # Assume data is complete for demo
            )
            db.session.add(inquiry)
            db.session.flush() # Flush to get the inquiry.id assigned by the DB
            click.echo(f"Inquiry created with ID: {inquiry.id}")

            # 2. Create ExtractedData linked to Inquiry
            click.echo("Creating ExtractedData...")
            extracted_data = ExtractedData(
                inquiry_id=inquiry.id,
                data=sample_data,
                extraction_source="manual_sample",
                validation_status="Complete", # Matches Inquiry status
                missing_fields=None
            )
            db.session.add(extracted_data)
            click.echo(f"ExtractedData prepared (linked to Inquiry {inquiry.id})")

            # 3. Create a sample Email linked to Inquiry
            click.echo("Creating sample Email...")
            # Check if email already exists first
            existing_email = Email.query.get(sample_graph_id)
            if existing_email:
                click.echo(f"Sample email {sample_graph_id} already exists. Linking to inquiry {inquiry.id} if not already linked.")
                if not existing_email.inquiry_id:
                    existing_email.inquiry_id = inquiry.id
            else:
                email = Email(
                    graph_id=sample_graph_id,
                    subject="Sample Quote Request for Demo",
                    sender_address=sample_email_address, # Match inquiry
                    sender_name="Test Customer",
                    received_at=datetime.now(timezone.utc) - timedelta(hours=2), # Sample time
                    inquiry_id=inquiry.id, # Link to the inquiry
                    processing_status='processed' # Mark as processed
                )
                db.session.add(email)
                click.echo(f"Email created with Graph ID: {email.graph_id} (linked to Inquiry {inquiry.id})")

            # 4. Commit the session
            click.echo("Committing transaction...")
            db.session.commit()
            click.secho("Successfully created and committed sample data.", fg='green')

        except Exception as e:
            click.secho(f"An error occurred: {e}", fg='red')
            click.echo("Rolling back transaction...")
            db.session.rollback() 