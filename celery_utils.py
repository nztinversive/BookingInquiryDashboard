import os
from celery import Celery
from config import config_by_name

def make_celery():
    """Creates and configures a Celery application instance."""
    # Determine config name (e.g., 'development', 'production')
    config_name = os.getenv('FLASK_CONFIG') or 'default'
    flask_config = config_by_name[config_name]

    # Create Celery instance
    # We use 'tasks' as the main name, adjust if your tasks live elsewhere
    celery_app = Celery('tasks', 
                        broker=flask_config.CELERY_BROKER_URL, 
                        backend=flask_config.CELERY_RESULT_BACKEND,
                        include=['tasks'] # List of modules to import tasks from
                        )

    # Update Celery config from Flask config object
    # This makes Celery config consistent with Flask
    celery_app.conf.update(
        # Example: Add task-specific settings if needed later
        # task_serializer='json',
        # result_serializer='json',
        # accept_content=['json'],
        beat_schedule = {
            'poll-emails-every-2-minutes': {
                'task': 'tasks.poll_and_dispatch_emails', # Name of the polling task
                'schedule': 120.0, # Run every 120 seconds (2 minutes)
                # 'args': (), # Add args if the task requires them
                # 'options': {'expires': 15.0}, # Optional: task expires if not started in 15s
            },
            # Add other periodic tasks here if needed
        },
        timezone='UTC', # Explicitly set timezone for beat schedule
    )

    return celery_app

# Create the celery instance globally in this module
celery = make_celery() 