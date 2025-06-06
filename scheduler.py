import os
import time
from redis import Redis
from rq_scheduler import Scheduler
from datetime import timedelta

# Import the Flask app and the polling function
from web_app import app
from app.background_tasks import poll_new_emails

# Ensure Redis connection uses environment variables or default
redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
conn = Redis.from_url(redis_url)

# Create a scheduler instance
scheduler = Scheduler(connection=conn)

def schedule_polling_job(app_context):
    \"\"\"Schedules the poll_new_emails job using interval from app config.\"\"\"
    # Get interval from config within the app context
    poll_interval = app_context.config.get('POLL_INTERVAL_SECONDS', 120)
    print(f"Attempting to schedule poll_new_emails every {poll_interval} seconds...")
    
    # Clear any existing jobs for this function to avoid duplicates if scheduler restarts
    # Note: This might clear jobs scheduled with different intervals/args if the function name is reused.
    # Consider using a unique job ID if more complex scheduling is needed.
    for job in scheduler.get_jobs():
        if job.func_name == 'app.background_tasks.poll_new_emails':
            print(f"Clearing existing scheduled job: {job.id}")
            scheduler.cancel(job)

    # Schedule the job to run periodically
    job = scheduler.schedule(
        scheduled_time=timedelta(seconds=1), # Start almost immediately
        func='app.background_tasks.poll_new_emails', # Path to function
        args=[app], # Pass the app instance as an argument
        interval=poll_interval, # Use interval from config
        repeat=None, # Repeat indefinitely
        queue_name='default' # Schedule on the default RQ queue (worker will pick it up)
    )
    print(f"Scheduled job {job.id} to run poll_new_emails.")

if __name__ == '__main__':
    # Need app context to ensure config/extensions are loaded if poll_new_emails needs them immediately
    # although the job itself runs within the worker's app context later.
    with app.app_context():
        schedule_polling_job(app) # Pass app instance to scheduling function
        
        # The rq-scheduler command is typically run separately.
        # This script is mainly for defining and initially scheduling the job.
        # To run the scheduler process continuously, use the command line:
        print("\nJob scheduled. Now run the scheduler process in your terminal:")
        print("rqscheduler --url $REDIS_URL") 
        print("(Ensure REDIS_URL is set or replace with redis://localhost:6379)")
        # Example of running scheduler loop (less common than using rqscheduler command):
        # print("Starting scheduler loop (alternative to rqscheduler command)...")
        # while True:
        #     # scheduler.run(burst=True) # Check for jobs and run scheduler logic once
        #     # time.sleep(60) # Check every minute
        #     pass # Keep script alive if needed, but rqscheduler command is preferred 