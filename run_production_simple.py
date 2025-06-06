#!/usr/bin/env python3
"""
Simple production startup script for Replit deployment.
Runs the background worker in a separate thread within the same process.
This is more reliable in Replit's environment than multiprocessing.
"""

import os
import sys
import threading
import time
import logging
import signal
import atexit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(threadName)s] - %(message)s'
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown_flag = threading.Event()

def run_background_worker():
    """Run the background worker in a separate thread"""
    logger.info("Starting background worker thread...")
    
    try:
        # Import and initialize the worker
        from postgres_worker import process_pending_tasks, initialize_worker_app
        
        # Initialize the worker
        app_instance, db_session_factory_instance = initialize_worker_app()
        logger.info("Background worker initialized successfully")
        
        # Start processing tasks with a modified loop that respects shutdown_flag
        logger.info("Background worker thread ready to process tasks")
        
        # Import the worker loop logic but run it in a controlled way
        from postgres_worker import WORKER_LOOP_SLEEP_SECONDS, MAX_TASK_RETRIES, RETRY_BACKOFF_BASE_SECONDS
        from postgres_worker import PendingTask, OperationalError, datetime, timezone, timedelta, text
        from sqlalchemy.exc import IntegrityError
        
        # Run the task processing loop with shutdown checking
        while not shutdown_flag.is_set():
            task_to_process = None
            db_sess = None
            
            try:
                with app_instance.app_context():
                    db_sess = db_session_factory_instance()
                    
                    # Fetch a pending task (simplified version of the original logic)
                    stmt = text(
                        "SELECT id FROM pending_tasks "
                        "WHERE status = 'pending' AND scheduled_for <= :now "
                        "ORDER BY scheduled_for ASC, id ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
                    )
                    result = db_sess.execute(stmt, {"now": datetime.now(timezone.utc)}).fetchone()
                    
                    if result:
                        task_id = result[0]
                        task_to_process = db_sess.get(PendingTask, task_id)
                        
                        if task_to_process:
                            logger.info(f"[Task {task_id}] Processing task type: {task_to_process.task_type}")
                            
                            # Update task status
                            task_to_process.status = 'processing'
                            task_to_process.attempts += 1
                            db_sess.commit()
                            
                            # Process the task
                            from app.background_tasks import handle_task
                            handle_task_result = handle_task(task_to_process.task_type, task_to_process.payload or {}, app_instance)
                            
                            # Mark as successful
                            task_to_process.status = 'success'
                            task_to_process.processed_at = datetime.now(timezone.utc)
                            task_to_process.last_error = None
                            db_sess.commit()
                            logger.info(f"[Task {task_id}] Successfully completed")
                    
            except Exception as e:
                logger.error(f"Error in background worker: {e}", exc_info=True)
                if db_sess:
                    try:
                        db_sess.rollback()
                    except:
                        pass
            finally:
                if db_sess:
                    try:
                        db_session_factory_instance.remove()
                    except:
                        pass
            
            # Sleep or check for shutdown
            for _ in range(WORKER_LOOP_SLEEP_SECONDS):
                if shutdown_flag.is_set():
                    break
                time.sleep(1)
    
    except Exception as e:
        logger.error(f"Fatal error in background worker thread: {e}", exc_info=True)
    
    logger.info("Background worker thread stopped")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_flag.set()

def cleanup():
    """Cleanup function"""
    logger.info("Cleanup initiated")
    shutdown_flag.set()

def main():
    """Main function"""
    # Set default environment variables if not present
    if not os.getenv('SESSION_SECRET'):
        logger.info("Setting default SESSION_SECRET for development...")
        os.environ['SESSION_SECRET'] = 'dev-secret-key-production-change-this'
    
    # Register signal handlers and cleanup
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup)
    
    logger.info("Starting production application with integrated background worker...")
    
    try:
        # Start background worker in a separate thread
        worker_thread = threading.Thread(target=run_background_worker, name="BackgroundWorker", daemon=True)
        worker_thread.start()
        logger.info("Background worker thread started")
        
        # Give the worker thread a moment to initialize
        time.sleep(2)
        
        # Start the Flask web server in the main thread
        logger.info("Starting Flask web server in main thread...")
        from main import app
        
        # Run the Flask app (this blocks until shutdown)
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False, threaded=True)
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Error in main process: {e}", exc_info=True)
    finally:
        logger.info("Shutting down...")
        shutdown_flag.set()

if __name__ == "__main__":
    main() 