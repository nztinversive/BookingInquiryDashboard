import os
from dotenv import load_dotenv

# Load environment variables from .env file, if it exists
load_dotenv()

class Config:
    """Base configuration class."""
    # Flask settings - Read from SESSION_SECRET env var
    SECRET_KEY = os.environ.get('SESSION_SECRET', 'a_very_secret_key_you_should_change')

    # SQLAlchemy settings
    # Silence the deprecation warning
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Improve connection handling
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Application specific settings (can be overridden)
    # Add any other default config values here

    # RQ/Redis Configuration
    REDIS_URL = os.environ.get('REDIS_URL') or 'redis://localhost:6379/0'
    POLL_INTERVAL_SECONDS = int(os.environ.get('POLL_INTERVAL_SECONDS') or 120)

class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    ENV = 'development' # Deprecated in Flask 2.3, but still useful for clarity

    # Database URL (allow fallback to SQLite for easier dev setup)
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if DATABASE_URL:
         # Ensure correct scheme for SQLAlchemy
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    else:
        # Fallback to SQLite if DATABASE_URL is not set
        SQLALCHEMY_DATABASE_URI = 'sqlite:///dev_database.db'
        print("WARNING: DATABASE_URL not set. Using SQLite database: dev_database.db") # Use print for visibility

    # OpenAI API Key (optional for development if not testing extraction)
    # Read from OPEN_API_KEY env var
    OPENAI_API_KEY = os.environ.get('OPEN_API_KEY')
    if not OPENAI_API_KEY:
        print("WARNING: OPEN_API_KEY not set. OpenAI features will be disabled.")

    # MS Graph API Credentials (optional for development if not testing email)
    # Read from MS365_* env vars
    MS_GRAPH_CLIENT_ID = os.environ.get('MS365_CLIENT_ID')
    MS_GRAPH_CLIENT_SECRET = os.environ.get('MS365_CLIENT_SECRET')
    MS_GRAPH_TENANT_ID = os.environ.get('MS365_TENANT_ID')
    MS_GRAPH_MAILBOX_USER_ID = os.environ.get('MS365_TARGET_EMAIL') # Mailbox to monitor

    if not all([MS_GRAPH_CLIENT_ID, MS_GRAPH_CLIENT_SECRET, MS_GRAPH_TENANT_ID, MS_GRAPH_MAILBOX_USER_ID]):
         print("WARNING: One or more MS Graph environment variables (MS365_CLIENT_ID, MS365_CLIENT_SECRET, MS365_TENANT_ID, MS365_TARGET_EMAIL) are not set. Email polling will likely fail.")


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    ENV = 'production' # Deprecated in Flask 2.3

    # Enforce essential environment variables in production
    # Read from SESSION_SECRET env var
    SECRET_KEY = os.environ['SESSION_SECRET'] # Raises KeyError if not set
    DATABASE_URL = os.environ['DATABASE_URL'] # Raises KeyError if not set
    # Read from OPEN_API_KEY env var
    OPENAI_API_KEY = os.environ['OPEN_API_KEY'] # Raises KeyError if not set

    # Ensure correct scheme for SQLAlchemy
    if DATABASE_URL.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    else:
         SQLALCHEMY_DATABASE_URI = DATABASE_URL

    # MS Graph API Credentials (Required)
    # Read from MS365_* env vars
    MS_GRAPH_CLIENT_ID = os.environ['MS365_CLIENT_ID']
    MS_GRAPH_CLIENT_SECRET = os.environ['MS365_CLIENT_SECRET']
    MS_GRAPH_TENANT_ID = os.environ['MS365_TENANT_ID']
    MS_GRAPH_MAILBOX_USER_ID = os.environ['MS365_TARGET_EMAIL']


# Dictionary to easily access config classes by name
config_by_name = dict(
    development=DevelopmentConfig,
    production=ProductionConfig,
    default=DevelopmentConfig
) 