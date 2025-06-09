# Standard library imports
import os
import logging
import atexit
from datetime import datetime, timezone, timedelta # Added datetime
import json # Added json

# Third-party imports
from flask import Flask
import click # Added click for CLI commands
from flask.cli import with_appcontext # Added for CLI context
import arrow # Added for datetime humanization
from .whatsapp_routes import whatsapp_bp

# Import config
from config import config_by_name # Import the config dictionary

# Import extensions from the new file
from .extensions import db, login_manager, migrate, scheduler # Added scheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

# --- Configure Logging ---
# Set level to DEBUG to capture detailed filter logs
logging.basicConfig(level=logging.INFO, # Changed to INFO for less verbosity in prod
                    format='%(asctime)s - %(levelname)s - %(name)s - %(threadName)s - %(message)s')

# --- Application Factory Function ---
def create_app():
    """Create and configure an instance of the Flask application."""
    # Explicitly set static folder relative to the 'app' directory
    app = Flask(__name__, 
                instance_relative_config=False,
                static_folder='../static', # Go up one level from 'app' directory
                static_url_path='/static') # Default, but set explicitly

    # --- Configuration ---
    # Load configuration based on FLASK_ENV environment variable
    # Default to 'development' if FLASK_ENV is not set
    env_name = os.getenv('FLASK_ENV', 'development')
    try:
        app.config.from_object(config_by_name[env_name])
        logging.info(f"Loading configuration for environment: {env_name}")
    except KeyError:
        logging.warning(f"Invalid FLASK_ENV value: '{env_name}'. Falling back to default (development) configuration.")
        app.config.from_object(config_by_name['default'])

    # --- Process and Validate Configuration ---
    # Process DB URI after loading
    db_url = app.config.get('DATABASE_URL')
    if db_url and db_url.startswith("postgres://"):
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url.replace("postgres://", "postgresql://", 1)
    elif db_url:
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    elif app.config.get('ENV') != 'production': # Fallback for non-prod if DATABASE_URL missing
        app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///dev_database.db')
        logging.warning(f"DATABASE_URL not set. Using fallback or SQLALCHEMY_DATABASE_URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
    # Production MUST have DATABASE_URL (checked below)

    # Add checks for required variables in production
    if app.config.get('ENV') == 'production':
        required_prod_vars = [
            'SECRET_KEY', 'DATABASE_URL', 'OPENAI_API_KEY',
            'MS_GRAPH_CLIENT_ID', 'MS_GRAPH_CLIENT_SECRET', 'MS_GRAPH_TENANT_ID', 'MS_GRAPH_MAILBOX_USER_ID',
            # 'WAAPI_API_TOKEN', 'WAAPI_INSTANCE_ID' # Assuming WhatsApp might be optional
        ]
        missing_vars = [var for var in required_prod_vars if not app.config.get(var)]
        if missing_vars:
            raise ValueError(f"Missing required production environment variables: {', '.join(missing_vars)}")
        # Also ensure the final DB URI got set
        if not app.config.get('SQLALCHEMY_DATABASE_URI'):
             raise ValueError("SQLALCHEMY_DATABASE_URI could not be determined in production.")

    # Add checks for development environment (warnings for optional)
    elif app.config.get('ENV') == 'development':
        # Check for optional development dependencies/configs
        # (DB URL fallback already handled above)
        if not app.config.get('OPENAI_API_KEY'):
             logging.warning("OPEN_API_KEY not set. OpenAI features will be disabled.")
        if not all([app.config.get('MS_GRAPH_CLIENT_ID'), app.config.get('MS_GRAPH_CLIENT_SECRET'), app.config.get('MS_GRAPH_TENANT_ID'), app.config.get('MS_GRAPH_MAILBOX_USER_ID')]):
             logging.warning("One or more MS Graph environment variables (...) are not set. Email polling will likely fail.")
        if not all([app.config.get('WAAPI_API_TOKEN'), app.config.get('WAAPI_INSTANCE_ID')]):
             logging.warning("WAAPI_API_TOKEN or WAAPI_INSTANCE_ID not set. WhatsApp features may be disabled or fail.")

    # Log final database URI being used
    db_uri_log = app.config.get('SQLALCHEMY_DATABASE_URI', 'Not Set')
    if 'sqlite' not in db_uri_log: # Avoid logging full credentials for remote DBs
        try:
            from urllib.parse import urlparse
            parsed_uri = urlparse(db_uri_log)
            db_uri_log = f"{parsed_uri.scheme}://{parsed_uri.hostname}:{parsed_uri.port}/{parsed_uri.path}" 
        except Exception:
            db_uri_log = "[Could not parse DB URI for logging]"
    logging.info(f"Database URI set to: {db_uri_log}")

    # Add SQLAlchemy engine options for connection pooling
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 290,  # Recycle connections slightly before a potential 5-min timeout
    }
    logging.info(f"SQLAlchemy engine options configured with pool_pre_ping=True, pool_recycle=290.")

    # --- Initialize Extensions with App ---
    db.init_app(app)
    login_manager.init_app(app)
    # Setup login view after initializing
    login_manager.login_view = 'auth.login' 
    migrate.init_app(app, db) 

    # --- Application Context ---
    with app.app_context():
        # --- Import Blueprints ---
        from . import routes  # Import main application routes
        from .auth import auth_bp # Import authentication blueprint
        from .whatsapp_routes import whatsapp_bp
        # Import other blueprints as needed

        # --- Register Blueprints ---
        app.register_blueprint(routes.main_bp)
        app.register_blueprint(auth_bp, url_prefix='/auth')
        app.register_blueprint(whatsapp_bp)
        # Register other blueprints

        # --- Initialize Database ---
        logging.info("Initializing database tables within app context...")
        try:
            # Check if DB URI is actually set before trying create_all
            if app.config.get('SQLALCHEMY_DATABASE_URI'):
                db.create_all()
                logging.info("Database tables checked/created.")
            else:
                logging.warning("Skipping db.create_all() because SQLALCHEMY_DATABASE_URI is not configured.")
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

        # --- Custom Template Filters ---
        def humanize_datetime_filter(dt, default_if_none="N/A"):
            if dt is None:
                return default_if_none
            try:
                # Ensure datetime is timezone-aware (UTC if naive)
                if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
                    dt = dt.replace(tzinfo=timezone.utc) # Assume UTC if naive
                return arrow.get(dt).humanize()
            except Exception as e:
                logging.warning(f"Error humanizing datetime '{dt}': {e}")
                # Fallback to string representation or a placeholder
                return dt.strftime("%Y-%m-%d %H:%M") if isinstance(dt, datetime) else default_if_none

        app.jinja_env.filters['humanize_datetime'] = humanize_datetime_filter
        logging.info("Registered custom Jinja filter: humanize_datetime")
        
        def format_contact_filter(email_address):
            """Format email addresses for display, especially WhatsApp ones"""
            if not email_address:
                return "No contact"
            
            # Check if it's a WhatsApp email
            if '@internal.placeholder' in email_address and 'whatsapp_' in email_address:
                # Extract the phone number from whatsapp_PHONENUMBER@c.us@internal.placeholder
                try:
                    # Extract phone number between 'whatsapp_' and '@c.us'
                    phone_part = email_address.replace('whatsapp_', '').split('@')[0]
                    if len(phone_part) >= 10:  # Valid phone number length
                        # Format as (XXX) XXX-XXXX for US numbers or +XX XXX XXX XXXX for international
                        if len(phone_part) == 11 and phone_part.startswith('1'):
                            # US number with country code
                            formatted = f"({phone_part[1:4]}) {phone_part[4:7]}-{phone_part[7:]}"
                            return f"📱 WhatsApp: {formatted}"
                        elif len(phone_part) == 10:
                            # US number without country code
                            formatted = f"({phone_part[:3]}) {phone_part[3:6]}-{phone_part[6:]}"
                            return f"📱 WhatsApp: {formatted}"
                        else:
                            # International number
                            return f"📱 WhatsApp: +{phone_part}"
                    else:
                        return "📱 WhatsApp Contact"
                except Exception:
                    return "📱 WhatsApp Contact"
            
            # Regular email - truncate if too long
            if len(email_address) > 25:
                return f"✉️ {email_address[:22]}..."
            else:
                return f"✉️ {email_address}"

        app.jinja_env.filters['format_contact'] = format_contact_filter
        logging.info("Registered custom Jinja filter: format_contact")

        # --- Custom CLI Commands ---
        register_cli_commands(app)

        # --- Configure and Start APScheduler ---
        ms_graph_config_ok = all([
            app.config.get('MS_GRAPH_CLIENT_ID'),
            app.config.get('MS_GRAPH_CLIENT_SECRET'),
            app.config.get('MS_GRAPH_TENANT_ID'),
            app.config.get('MS_GRAPH_MAILBOX_USER_ID')
        ])
        openai_config_ok = bool(app.config.get('OPENAI_API_KEY'))
        db_uri_ok = bool(app.config.get('SQLALCHEMY_DATABASE_URI'))

        if ms_graph_config_ok and openai_config_ok and db_uri_ok:
            logging.info("Required configurations for background tasks and DB are present.")
            try:
                from ms_graph_service import configure_ms_graph_client
                from data_extraction_service import configure_openai_client
                from .background_tasks import trigger_email_polling_task_creation

                configure_ms_graph_client(app.config)
                configure_openai_client(app.config)
                logging.info("MS Graph and OpenAI clients configured.")

                # Configure APScheduler
                jobstore_url = app.config['SQLALCHEMY_DATABASE_URI']
                jobstore_successfully_configured = False
                try:
                    # Check if jobstore named 'default' already exists to avoid ValueError
                    if 'default' not in scheduler.get_jobstores():
                        scheduler.add_jobstore(SQLAlchemyJobStore(url=jobstore_url), alias='default')
                        logging.info("APScheduler 'default' job store added/configured.")
                    else:
                        logging.info("APScheduler 'default' job store was already configured.")
                    jobstore_successfully_configured = True
                except Exception as e:
                    # Catch any other exceptions during job store configuration
                    logging.error(f"Error configuring/checking APScheduler job store 'default': {e}", exc_info=True)
                    jobstore_successfully_configured = False

                if jobstore_successfully_configured:
                    # Check if scheduler is already running to prevent errors on re-creation
                    if not scheduler.running:
                        try:
                            scheduler.start(paused=False)
                            logging.info("APScheduler started successfully.")
                            # Ensure the scheduler shuts down when the app exits
                            atexit.register(lambda: scheduler.shutdown())
                        except Exception as e:
                            logging.error(f"Failed to start APScheduler: {e}", exc_info=True)
                    else:
                        logging.info("APScheduler was already running.")

            except ImportError as import_err:
                 logging.error(f"Could not import necessary service or task modules for scheduler: {import_err}")
            except Exception as startup_err:
                logging.error(f"Error during APScheduler startup or job scheduling: {startup_err}", exc_info=True)
        else:
            missing_configs_for_scheduler = []
            if not ms_graph_config_ok: missing_configs_for_scheduler.append("MS Graph")
            if not openai_config_ok: missing_configs_for_scheduler.append("OpenAI")
            if not db_uri_ok: missing_configs_for_scheduler.append("Database URI")
            logging.warning(f"APScheduler WILL NOT be started due to missing configuration for: {', '.join(missing_configs_for_scheduler)}")

        # --- Return App Instance ---
        logging.info("Flask app created successfully.")
        return app

# --- CLI Command Definitions ---
def register_cli_commands(app):
    """Register custom CLI commands for the application."""
    
    @app.cli.command('fix-tasks')
    @with_appcontext
    def fix_stuck_whatsapp_tasks_command():
        """Finds and fixes pending WhatsApp tasks with the old task_type."""
        click.echo("--- Starting Task Fix Script ---")
        
        from .models import PendingTask
        
        old_task_type = 'process_whatsapp_message'
        new_task_type = 'new_whatsapp_message'
        time_threshold = datetime.now(timezone.utc) - timedelta(seconds=10)

        try:
            total_pending = db.session.query(PendingTask).filter_by(status='pending').count()
            click.echo(f"Successfully connected to DB. Found {total_pending} total pending task(s).")
            
            tasks_to_fix = db.session.query(PendingTask).filter(
                PendingTask.task_type == old_task_type,
                PendingTask.status == 'pending',
                PendingTask.created_at <= time_threshold
            ).all()

            if not tasks_to_fix:
                click.echo(f"Query executed. No stuck tasks found with type '{old_task_type}'.")
                click.echo("--- Script Finished ---")
                return

            click.echo(f"Found {len(tasks_to_fix)} stuck WhatsApp task(s). Updating them now...")

            updated_count = 0
            for task in tasks_to_fix:
                task.task_type = new_task_type
                updated_count += 1
            
            db.session.commit()

            click.echo(f"Successfully updated {updated_count} task(s) to type '{new_task_type}'.")
            click.echo("The background worker should now be able to process these tasks.")

        except Exception as e:
            db.session.rollback()
            click.echo(click.style(f"An error occurred during database operation: {e}", fg='red'))
            click.echo("Database transaction has been rolled back.")
        
        click.echo("--- Script Finished ---")


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