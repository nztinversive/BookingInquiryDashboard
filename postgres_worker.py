import logging
import os
import time
from datetime import datetime, timezone, timedelta
import json

import psycopg2 # For potential direct DB error handling, though SQLAlchemy is primary
from sqlalchemy import create_engine, text, func
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.exc import OperationalError, IntegrityError

# Attempt to import the Flask app instance and task handler
# This assumes your Flask app is created by create_app() in app/__init__.py
# and that the worker can access it.
# If your app structure is different, this import will need adjustment.
try:
    from app import create_app, db as flask_db # flask_db is the SQLAlchemy instance from extensions
    from app.models import PendingTask # Import the PendingTask model
    from app.background_tasks import handle_task # The dispatcher function
except ImportError as e:
    logging.basicConfig(level=logging.ERROR)
    logging.error(f"Failed to import Flask app components: {e}. Ensure PYTHONPATH is set correctly or worker is run from project root.")
    logging.error("The worker cannot start without the Flask app and its components.")
    exit(1)

# --- Configuration ---
WORKER_LOOP_SLEEP_SECONDS = int(os.getenv('PG_WORKER_SLEEP_SECONDS', '5'))
MAX_TASK_RETRIES = int(os.getenv('PG_WORKER_MAX_RETRIES', '3'))
RETRY_BACKOFF_BASE_SECONDS = int(os.getenv('PG_WORKER_RETRY_BACKOFF_BASE', '60')) # 1 minute base for backoff

# Setup basic logging for the worker script itself
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(process)d - Thread-%(thread)d - %(message)s')
logger = logging.getLogger(__name__)

flask_app = None
Session = None

def initialize_worker_app():
    """Initializes the Flask app and database session for the worker."""
    global flask_app, Session
    if flask_app is None:
        logger.info("Initializing Flask app for worker...")
        flask_app = create_app() # Assumes create_app() exists and configures DB, logging etc.
        flask_app.app_context().push() # Push an app context for operations outside requests
        logger.info("Flask app initialized.")

    if Session is None and flask_db is not None:
        logger.info("Configuring scoped session for worker DB operations...")
        # Use the existing engine from flask_db.engine if available
        # Otherwise, create a new one from Flask app config
        engine = flask_db.engine
        Session = scoped_session(sessionmaker(bind=engine))
        logger.info("Scoped session configured.")
    elif Session is None:
        logger.error("flask_db (SQLAlchemy instance) is not available. Cannot configure session.")
        raise RuntimeError("Failed to configure database session for worker.")
    return flask_app, Session

def process_pending_tasks(app, db_session_factory):
    """Main loop for the worker to poll and process tasks."""
    logger.info(f"Postgres Worker started. Polling every {WORKER_LOOP_SLEEP_SECONDS}s. Max retries: {MAX_TASK_RETRIES}.")

    while True:
        task_to_process = None
        db_sess = None # Ensure session is fresh per loop/task attempt
        try:
            db_sess = db_session_factory() # Get a new session from the scoped session factory

            # Atomically fetch and lock a pending task
            # Tasks are ordered by scheduled_for, then by id to ensure FIFO for tasks scheduled at the same time
            # Ensure `PendingTask` has `scheduled_for` and `id` attributes.
            # The `FOR UPDATE SKIP LOCKED` is PostgreSQL specific and prevents other workers from picking the same row.
            stmt = (
                text(
                    "SELECT id FROM pending_tasks "
                    "WHERE status = 'pending' AND scheduled_for <= :now "
                    "ORDER BY scheduled_for ASC, id ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
                )
            )
            result = db_sess.execute(stmt, {"now": datetime.now(timezone.utc)}).fetchone()

            if result:
                task_id = result[0]
                task_to_process = db_sess.get(PendingTask, task_id)

                if task_to_process:
                    logger.info(f"[Task {task_id}] Claimed task. Type: {task_to_process.task_type}. Attempts: {task_to_process.attempts}", flush=True)
                    task_to_process.status = 'processing'
                    task_to_process.attempts += 1
                    db_sess.commit() # Commit status change before processing

                    task_payload = task_to_process.payload or {}
                    task_type = task_to_process.task_type

                    try:
                        logger.info(f"[Task {task_id}] Executing task type: {task_type}", flush=True)
                        # The handle_task function needs the Flask app instance for its own context
                        handle_task_result = handle_task(task_type, task_payload, app)
                        task_to_process.status = 'success'
                        task_to_process.processed_at = datetime.now(timezone.utc)
                        task_to_process.last_error = None
                        logger.info(f"[Task {task_id}] Successfully processed. Result: {handle_task_result}", flush=True)
                    except Exception as task_exec_err:
                        logger.error(f"[Task {task_id}] Error executing task: {task_exec_err}", exc_info=True, flush=True)
                        task_to_process.last_error = str(task_exec_err)
                        if task_to_process.attempts >= MAX_TASK_RETRIES:
                            task_to_process.status = 'failed' # Max retries reached
                            logger.warning(f"[Task {task_id}] Max retries ({MAX_TASK_RETRIES}) reached. Marking as failed.", flush=True)
                        else:
                            task_to_process.status = 'pending' # Revert to pending for retry
                            # Exponential backoff for retry
                            backoff_seconds = RETRY_BACKOFF_BASE_SECONDS * (2 ** (task_to_process.attempts -1))
                            task_to_process.scheduled_for = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
                            logger.info(f"[Task {task_id}] Scheduled for retry at {task_to_process.scheduled_for.isoformat()} (attempt {task_to_process.attempts}).", flush=True)
                    finally:
                        # Always commit the final state of the task (success, failed, or pending for retry)
                        if db_sess.is_active:
                             db_sess.commit()
                else:
                    # This case should be rare if the SELECT FOR UPDATE worked as expected
                    logger.warning("Claimed a task ID but could not fetch the task object. Race condition or DB issue?", flush=True)
            else:
                # No tasks found, sleep before polling again
                # logger.debug("No pending tasks found. Sleeping...") # Too verbose for INFO
                pass # Fall through to sleep

        except OperationalError as op_err:
            logger.error(f"Database operational error: {op_err}. Retrying connection or sleeping.", exc_info=True, flush=True)
            if db_sess and db_sess.is_active:
                db_sess.rollback() # Rollback on DB errors
            time.sleep(WORKER_LOOP_SLEEP_SECONDS * 2) # Longer sleep on DB connection issues
        except Exception as e:
            logger.error(f"An unexpected error occurred in the worker loop: {e}", exc_info=True, flush=True)
            if db_sess and db_sess.is_active:
                db_sess.rollback()
            # Avoid tight loop on unexpected persistent errors
            time.sleep(WORKER_LOOP_SLEEP_SECONDS)
        finally:
            if db_sess:
                db_session_factory.remove() # Properly close/remove the scoped session

        # If no task was processed, or after processing one, sleep.
        if not result: # Only sleep if no task was found to process immediately
            time.sleep(WORKER_LOOP_SLEEP_SECONDS)

if __name__ == "__main__":
    print("POSTGRES_WORKER_SCRIPT_STARTED_EXECUTION_V2", flush=True) # New, distinct print
    # Configure logging
    log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
    app_instance = None
    db_session_factory_instance = None
    try:
        app_instance, db_session_factory_instance = initialize_worker_app()
        logger.info("Worker initialization complete. Starting task processing loop.")
    except Exception as init_err:
        logger.critical(f"Worker failed to initialize: {init_err}", exc_info=True, flush=True)
        exit(1)

    if app_instance and db_session_factory_instance:
        process_pending_tasks(app_instance, db_session_factory_instance)
    else:
        logger.critical("Application or DB session factory not initialized. Worker cannot start.", flush=True)
        exit(1) 