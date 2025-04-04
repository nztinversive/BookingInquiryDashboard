# Standard library imports
import os
import logging
import atexit

# Third-party imports
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

# --- Initialize Extensions ---
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login' # Specify the login view for redirection

# --- Application Factory Function ---
def create_app():
    """Create and configure an instance of the Flask application."""
    app = Flask(__name__, instance_relative_config=False)

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
        # from . import models # Uncomment if models are defined here or need explicit import

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


        # --- Return App Instance ---
        logging.info("Flask app created successfully.")
        return app 