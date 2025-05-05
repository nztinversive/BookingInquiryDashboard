import os
import logging
import traceback
from datetime import datetime, timezone

from redis import Redis
from rq import Worker, Queue, Connection

# Import the Flask app instance
# Adjust the import path if your app instance is created elsewhere (e.g., from app import create_app)
from web_app import app 

# --- Custom RQ Exception Handler ---
def handle_failed_job(job, connection, type, value, tb):
    \"\"\"RQ exception handler to mark jobs as permanently failed in the DB.\"\"\"
    logger = logging.getLogger('rq.worker')
    
    # Extract email_graph_id from job args
    # Assuming process_email_job is the target and email_summary is the first arg
    email_graph_id = None
    if job.func_name == 'app.background_tasks.process_email_job' and job.args:
        try:
            email_summary = job.args[0]
            if isinstance(email_summary, dict):
                email_graph_id = email_summary.get('id')
        except (IndexError, TypeError, AttributeError) as e:
            logger.error(f"Could not extract email_graph_id from job {job.id} args: {e}. Job args: {job.args}")

    if email_graph_id:
        logger.warning(f"Job {job.id} (Email: {email_graph_id}) failed permanently. Type: {type.__name__}, Value: {value}")
        
        # Need app context to interact with DB
        # Create a fresh app context
        ctx = app.app_context()
        ctx.push()
        try:
            from app import db
            from app.models import Email

            email_record = db.session.get(Email, email_graph_id)
            if email_record:
                logger.info(f"Marking Email {email_graph_id} as 'permanently_failed' in DB.")
                email_record.processing_status = 'permanently_failed'
                # Store truncated error message
                error_details = f"{type.__name__}: {value}\n{traceback.format_exc(limit=5)}" # Limit traceback length
                email_record.processing_error = error_details[:2000] # Limit DB field size
                email_record.processed_at = datetime.now(timezone.utc)
                db.session.commit()
                logger.info(f"Successfully marked Email {email_graph_id} as failed.")
            else:
                logger.warning(f"Could not find Email {email_graph_id} in DB to mark as failed.")
                # Email might not have been created before failure
                # Optionally create a minimal failed record here?

        except Exception as db_err:
            logger.error(f"DB error while trying to mark Email {email_graph_id} as failed: {db_err}", exc_info=True)
            db.session.rollback() # Rollback on error
        finally:
            ctx.pop() # Pop the app context
            
    else:
        logger.error(f"Job {job.id} ({job.func_name}) failed permanently but could not determine email_graph_id. Type: {type.__name__}, Value: {value}")
        # Log traceback for non-email jobs or jobs where ID extraction failed
        logger.error(traceback.format_exc())

    # Return True to indicate the exception was handled (prevents default RQ failure handling)
    # Return False to let RQ move it to the failed queue as well (useful for monitoring)
    return False # Let RQ also move it to the failed queue

# Ensure Redis connection uses environment variables or default
# Match the connection details used in background_tasks.py
redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379') 
conn = Redis.from_url(redis_url)

# List of queues to listen to
listen = ['email_processing', 'default']

if __name__ == '__main__':
    # Push the Flask app context onto the stack for the worker
    # This makes current_app available within the jobs
    with app.app_context():
        with Connection(conn):
            # Pass the custom exception handler to the Worker
            worker = Worker(
                map(Queue, listen),
                connection=conn, 
                exception_handlers=[handle_failed_job] # Add handler here
            )
            print(f"Worker starting, listening on queues: {', '.join(listen)}")
            worker.work()
            print("Worker stopped.") 