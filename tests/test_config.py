import pytest
import os
from werkzeug.utils import import_string

# Assuming your Flask app factory is in main.py or app.py
# Adjust the import path as necessary if your app factory is elsewhere.
# from main import create_app # Or from app import create_app

# Placeholder for create_app if the exact location is unknown.
# We'''ll try to dynamically find it, or you may need to specify the correct import.
try:
    create_app = import_string('main:create_app')
except ImportError:
    try:
        create_app = import_string('app:create_app') # Common alternative
    except ImportError:
        # If create_app cannot be found, these tests will fail.
        # You might need to adjust the import above based on your project structure.
        pass 

# Also import your Config classes
# from config import DevelopmentConfig, ProductionConfig, Config # Adjust if needed

def test_development_config(monkeypatch):
    """Test development configuration loading."""
    monkeypatch.setenv('FLASK_ENV', 'development')
    monkeypatch.setenv('SESSION_SECRET', 'dev_secret')
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///test_dev.db')
    monkeypatch.setenv('OPENAI_API_KEY', 'dev_openai_key')
    monkeypatch.setenv('MS365_CLIENT_ID', 'dev_ms_client_id')
    monkeypatch.setenv('MS365_CLIENT_SECRET', 'dev_ms_client_secret')
    monkeypatch.setenv('MS365_TENANT_ID', 'dev_ms_tenant_id')
    monkeypatch.setenv('MS365_TARGET_EMAIL', 'dev_ms_target_email')
    monkeypatch.setenv('WAAPI_INSTANCE_ID', 'dev_wa_instance')
    monkeypatch.setenv('WAAPI_API_TOKEN', 'dev_wa_token')
    monkeypatch.setenv('WAAPI_WEBHOOK_SECRET', 'dev_wa_webhook_secret')
    
    # Assuming create_app exists and can be called with a config name
    # If your create_app doesn't take a config name, you might need to adapt
    # or ensure FLASK_ENV is sufficient.
    try:
        app = create_app(config_name='development')
    except NameError:
        pytest.fail("create_app function not found. Please check import paths.")


    assert app.config['DEBUG'] is True
    assert app.config['SECRET_KEY'] == 'dev_secret'
    assert 'sqlite:///test_dev.db' in app.config['SQLALCHEMY_DATABASE_URI']
    assert app.config['OPENAI_API_KEY'] == 'dev_openai_key'
    assert app.config['MS_GRAPH_CLIENT_ID'] == 'dev_ms_client_id'
    assert app.config['WAAPI_INSTANCE_ID'] == 'dev_wa_instance'
    assert app.config['WAAPI_API_TOKEN'] == 'dev_wa_token'
    assert app.config['WAAPI_WEBHOOK_SECRET'] == 'dev_wa_webhook_secret'

def test_production_config_success(monkeypatch):
    """Test production configuration loading with all required variables."""
    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.setenv('SESSION_SECRET', 'prod_secret')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@host/dbname')
    monkeypatch.setenv('OPENAI_API_KEY', 'prod_openai_key')
    monkeypatch.setenv('MS365_CLIENT_ID', 'prod_ms_client_id')
    monkeypatch.setenv('MS365_CLIENT_SECRET', 'prod_ms_client_secret')
    monkeypatch.setenv('MS365_TENANT_ID', 'prod_ms_tenant_id')
    monkeypatch.setenv('MS365_TARGET_EMAIL', 'prod_ms_target_email')
    monkeypatch.setenv('WAAPI_INSTANCE_ID', 'prod_wa_instance')
    monkeypatch.setenv('WAAPI_API_TOKEN', 'prod_wa_token')
    monkeypatch.setenv('WAAPI_WEBHOOK_SECRET', 'prod_wa_webhook_secret')

    try:
        app = create_app(config_name='production')
    except NameError:
        pytest.fail("create_app function not found. Please check import paths.")

    assert app.config['DEBUG'] is False
    assert app.config['SECRET_KEY'] == 'prod_secret'
    assert 'postgresql://user:pass@host/dbname' in app.config['SQLALCHEMY_DATABASE_URI']
    assert app.config['OPENAI_API_KEY'] == 'prod_openai_key'
    assert app.config['MS_GRAPH_CLIENT_ID'] == 'prod_ms_client_id'
    assert app.config['WAAPI_INSTANCE_ID'] == 'prod_wa_instance'
    assert app.config['WAAPI_API_TOKEN'] == 'prod_wa_token'
    assert app.config['WAAPI_WEBHOOK_SECRET'] == 'prod_wa_webhook_secret'

def test_production_config_missing_secret_key(monkeypatch):
    """Test production config raises ValueError if SESSION_SECRET is missing."""
    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.delenv('SESSION_SECRET', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@host/dbname')
    monkeypatch.setenv('OPENAI_API_KEY', 'prod_openai_key')
    # ... set other required vars ...
    monkeypatch.setenv('MS365_CLIENT_ID', 'prod_ms_client_id')
    monkeypatch.setenv('MS365_CLIENT_SECRET', 'prod_ms_client_secret')
    monkeypatch.setenv('MS365_TENANT_ID', 'prod_ms_tenant_id')
    monkeypatch.setenv('MS365_TARGET_EMAIL', 'prod_ms_target_email')
    monkeypatch.setenv('WAAPI_INSTANCE_ID', 'prod_wa_instance')
    monkeypatch.setenv('WAAPI_API_TOKEN', 'prod_wa_token')
    monkeypatch.setenv('WAAPI_WEBHOOK_SECRET', 'prod_wa_webhook_secret')

    with pytest.raises(ValueError, match="SESSION_SECRET environment variable must be set"):
        create_app(config_name='production')


def test_production_config_missing_database_url(monkeypatch):
    """Test production config raises ValueError if DATABASE_URL is missing."""
    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.setenv('SESSION_SECRET', 'prod_secret')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'prod_openai_key')
    # ... set other required vars ...
    monkeypatch.setenv('MS365_CLIENT_ID', 'prod_ms_client_id')
    monkeypatch.setenv('MS365_CLIENT_SECRET', 'prod_ms_client_secret')
    monkeypatch.setenv('MS365_TENANT_ID', 'prod_ms_tenant_id')
    monkeypatch.setenv('MS365_TARGET_EMAIL', 'prod_ms_target_email')
    monkeypatch.setenv('WAAPI_INSTANCE_ID', 'prod_wa_instance')
    monkeypatch.setenv('WAAPI_API_TOKEN', 'prod_wa_token')
    monkeypatch.setenv('WAAPI_WEBHOOK_SECRET', 'prod_wa_webhook_secret')
    
    with pytest.raises(ValueError, match="DATABASE_URL environment variable must be set in production"):
        create_app(config_name='production')

# Add similar tests for missing OPENAI_API_KEY, MS_GRAPH vars, and WAAPI vars in production.
# Example for one of the WAAPI vars:
def test_production_config_missing_waapi_token(monkeypatch):
    """Test production config behavior if WAAPI_API_TOKEN is missing."""
    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.setenv('SESSION_SECRET', 'prod_secret')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@host/dbname')
    monkeypatch.setenv('OPENAI_API_KEY', 'prod_openai_key')
    monkeypatch.setenv('MS365_CLIENT_ID', 'prod_ms_client_id')
    monkeypatch.setenv('MS365_CLIENT_SECRET', 'prod_ms_client_secret')
    monkeypatch.setenv('MS365_TENANT_ID', 'prod_ms_tenant_id')
    monkeypatch.setenv('MS365_TARGET_EMAIL', 'prod_ms_target_email')
    # WAAPI_INSTANCE_ID is set, WAAPI_WEBHOOK_SECRET is set, but WAAPI_API_TOKEN is missing
    monkeypatch.setenv('WAAPI_INSTANCE_ID', 'prod_wa_instance')
    monkeypatch.delenv('WAAPI_API_TOKEN', raising=False) 
    monkeypatch.setenv('WAAPI_WEBHOOK_SECRET', 'prod_wa_webhook_secret')

    # This test assumes that your ProductionConfig prints a WARNING for missing WAAPI vars,
    # rather than raising a ValueError. If it should raise an error, adjust the test.
    # For now, we'll just create the app and implicitly check that no error is raised
    # during app creation due to this specific missing variable, as per current config.py logic.
    try:
        app = create_app(config_name='production')
        # If you change config.py to raise ValueError for missing WAAPI vars,
        # this test should be changed to:
        # with pytest.raises(ValueError, match="All WAAPI environment variables must be set"):
        #    create_app(config_name='production')
        assert app.config['WAAPI_API_TOKEN'] is None # Or check for default if any
    except NameError:
        pytest.fail("create_app function not found. Please check import paths.")
    except ValueError as e:
        # This might catch the ValueError if you uncommented the raise in config.py
        # For now, we assume it doesn't raise for individual WAAPI keys
        pytest.fail(f"App creation failed unexpectedly for missing WAAPI_API_TOKEN: {e}")


# It's good practice to have a test directory with an __init__.py
# To ensure pytest discovers tests correctly.
# You might need to create tests/__init__.py if it doesn't exist. 