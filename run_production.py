#!/usr/bin/env python3
"""
Production startup script for Replit deployment.
Manages both the Flask web server and background worker processes.
"""

import os
import sys
import subprocess
import signal
import time
import logging
from multiprocessing import Process
import atexit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global process references for cleanup
web_process = None
worker_process = None

def start_web_server():
    """Start the Flask web server"""
    logger.info("Starting Flask web server...")
    try:
        # Import and run the Flask app
        from main import app
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Error starting web server: {e}")
        sys.exit(1)

def start_background_worker():
    """Start the background worker"""
    logger.info("Starting background worker...")
    try:
        # Run the postgres worker
        from postgres_worker import process_pending_tasks, initialize_worker_app
        
        # Initialize the worker
        app_instance, db_session_factory_instance = initialize_worker_app()
        logger.info("Background worker initialized successfully")
        
        # Start processing tasks
        process_pending_tasks(app_instance, db_session_factory_instance)
    except Exception as e:
        logger.error(f"Error starting background worker: {e}")
        sys.exit(1)

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, shutting down...")
    cleanup_processes()
    sys.exit(0)

def cleanup_processes():
    """Clean up child processes"""
    global web_process, worker_process
    
    if web_process and web_process.is_alive():
        logger.info("Terminating web server process...")
        web_process.terminate()
        web_process.join(timeout=5)
        if web_process.is_alive():
            web_process.kill()
    
    if worker_process and worker_process.is_alive():
        logger.info("Terminating background worker process...")
        worker_process.terminate()
        worker_process.join(timeout=5)
        if worker_process.is_alive():
            worker_process.kill()

def main():
    """Main function to start both processes"""
    global web_process, worker_process
    
    # Ensure required environment variables are set
    required_env_vars = ['SESSION_SECRET']
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logger.info("Setting default SESSION_SECRET for development...")
        os.environ['SESSION_SECRET'] = 'dev-secret-key-production-change-this'
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Register cleanup function
    atexit.register(cleanup_processes)
    
    logger.info("Starting production deployment with both web server and background worker...")
    
    try:
        # Start background worker process
        worker_process = Process(target=start_background_worker, name="BackgroundWorker")
        worker_process.start()
        logger.info(f"Background worker started with PID: {worker_process.pid}")
        
        # Give worker a moment to initialize
        time.sleep(2)
        
        # Start web server process
        web_process = Process(target=start_web_server, name="WebServer")
        web_process.start()
        logger.info(f"Web server started with PID: {web_process.pid}")
        
        # Monitor processes
        logger.info("Both processes started successfully. Monitoring...")
        
        while True:
            # Check if processes are still alive
            if not web_process.is_alive():
                logger.error("Web server process died! Restarting...")
                web_process = Process(target=start_web_server, name="WebServer")
                web_process.start()
                logger.info(f"Web server restarted with PID: {web_process.pid}")
            
            if not worker_process.is_alive():
                logger.error("Background worker process died! Restarting...")
                worker_process = Process(target=start_background_worker, name="BackgroundWorker")
                worker_process.start()
                logger.info(f"Background worker restarted with PID: {worker_process.pid}")
            
            # Wait before next check
            time.sleep(10)
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Error in main process: {e}")
    finally:
        cleanup_processes()

if __name__ == "__main__":
    main() 