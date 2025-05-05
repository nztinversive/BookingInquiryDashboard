import os
from redis import Redis
from rq import Worker, Queue, Connection

# Import the Flask app instance
# Adjust the import path if your app instance is created elsewhere (e.g., from app import create_app)
from web_app import app 

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
            worker = Worker(map(Queue, listen))
            print(f"Worker starting, listening on queues: {', '.join(listen)}")
            worker.work()
            print("Worker stopped.") 