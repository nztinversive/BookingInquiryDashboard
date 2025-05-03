import os
from dotenv import load_dotenv

# Load environment variables from .env file, if it exists
load_dotenv()

class Config:
    """Base configuration class."""
    # Flask settings
    SECRET_KEY = os.environ.get('SECRET_KEY', 'a_very_secret_key_you_should_change')

    # SQLAlchemy settings
    # Silence the deprecation warning
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Improve connection handling
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Application specific settings (can be overridden)
    # Add any other default config values here

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
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
    if not OPENAI_API_KEY:
        print("WARNING: OPENAI_API_KEY not set. OpenAI features will be disabled.")

    # MS Graph API Credentials (optional for development if not testing email)
    MS_GRAPH_CLIENT_ID = os.environ.get('MS_GRAPH_CLIENT_ID')
    MS_GRAPH_CLIENT_SECRET = os.environ.get('MS_GRAPH_CLIENT_SECRET')
    MS_GRAPH_TENANT_ID = os.environ.get('MS_GRAPH_TENANT_ID')
    MS_GRAPH_MAILBOX_USER_ID = os.environ.get('MS_GRAPH_MAILBOX_USER_ID') # The mailbox to monitor

    if not all([MS_GRAPH_CLIENT_ID, MS_GRAPH_CLIENT_SECRET, MS_GRAPH_TENANT_ID, MS_GRAPH_MAILBOX_USER_ID]):
         print("WARNING: One or more MS Graph environment variables (CLIENT_ID, CLIENT_SECRET, TENANT_ID, MAILBOX_USER_ID) are not set. Email polling will likely fail.")


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    ENV = 'production' # Deprecated in Flask 2.3

    # Enforce essential environment variables in production
    SECRET_KEY = os.environ['SECRET_KEY'] # Raises KeyError if not set
    DATABASE_URL = os.environ['DATABASE_URL'] # Raises KeyError if not set
    OPENAI_API_KEY = os.environ['OPENAI_API_KEY'] # Raises KeyError if not set

    # Ensure correct scheme for SQLAlchemy
    if DATABASE_URL.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    else:
         SQLALCHEMY_DATABASE_URI = DATABASE_URL

    # MS Graph API Credentials (Required)
    MS_GRAPH_CLIENT_ID = os.environ['MS_GRAPH_CLIENT_ID']
    MS_GRAPH_CLIENT_SECRET = os.environ['MS_GRAPH_CLIENT_SECRET']
    MS_GRAPH_TENANT_ID = os.environ['MS_GRAPH_TENANT_ID']
    MS_GRAPH_MAILBOX_USER_ID = os.environ['MS_GRAPH_MAILBOX_USER_ID']


# Dictionary to easily access config classes by name
config_by_name = dict(
    development=DevelopmentConfig,
    production=ProductionConfig,
    default=DevelopmentConfig
) 