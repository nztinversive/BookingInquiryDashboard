# Issue Resolution Plan: Redis Connection & Gunicorn Timeout

## 1. Problem Summary

Two main issues have been identified from the console logs:

1.  **Redis Connection Error (`redis.exceptions.ConnectionError`):**
    *   **Symptom:** `Error 99 connecting to localhost:6379. Cannot assign requested address.`
    *   **Impact:** This is a critical error. The application cannot connect to Redis, which is used as the message broker for RQ (Redis Queue). This prevents new email processing jobs from being enqueued, meaning incoming emails will not be processed by the background workers.
    *   **Likely Cause:** The application is defaulting to `localhost:6379` for Redis, but the Replit environment likely requires a specific `REDIS_URL` (provided as a secret/environment variable) that is not correctly set or picked up.

2.  **Gunicorn Worker Timeout:**
    *   **Symptom:** `[CRITICAL] WORKER TIMEOUT (pid:929)` during requests to the `/manual_email_poll` route.
    *   **Impact:** The manual trigger for email polling takes too long to complete within the Gunicorn web worker's default timeout, causing the request to be terminated prematurely. This affects the manual polling feature.
    *   **Likely Cause:** The `poll_new_emails` function, when called directly by the web route, performs potentially time-consuming operations (fetching, classifying multiple emails) in the web request-response cycle.

## 2. Resolution Steps

### Part 1: Fixing Redis Connection Error (Critical Priority)

**Objective:** Ensure the application correctly connects to the Redis instance provided by the Replit environment.

**Step 1.1: User Action - Verify `REDIS_URL` Environment Variable in Replit**
    *   **Action:** The user needs to verify that the `REDIS_URL` secret/environment variable is correctly configured in their Replit environment.
    *   **Details:**
        *   Navigate to the "Secrets" tab in the Replit sidebar.
        *   Ensure a secret named `REDIS_URL` exists.
        *   The value of `REDIS_URL` should be the connection string provided by Replit for the Redis database instance associated with the Repl. It will look something like `redis://<user>:<password>@<host>:<port>`.
    *   **Rationale:** The application code in `app/background_tasks.py` (`get_redis_conn`), `worker.py`, and `scheduler.py` correctly attempts to use `os.getenv('REDIS_URL', 'redis://localhost:6379')`. If `REDIS_URL` is not set or is incorrect, it defaults to `localhost:6379`, which is failing.

**Step 1.2: Code Review (Confirm Correct Usage - No Changes Expected)**
    *   **Files to Review:**
        *   `app/background_tasks.py` (specifically `get_redis_conn` and `get_email_queue`)
        *   `worker.py` (Redis connection setup: `conn = Redis.from_url(redis_url)`)
        *   `scheduler.py` (Redis connection setup: `conn = Redis.from_url(redis_url)`)
    *   **Action:** Confirm that these files consistently use `redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')` (or similar for port 0 or no port specified, Replit URLs usually include the port).
    *   **Status:** Based on previous reviews, the code correctly implements this. This step is a final verification.

**Step 1.3: User Action - Test Redis Connection**
    *   **Action:** After confirming/setting the `REDIS_URL` in Replit Secrets, redeploy/restart the application.
    *   **Verification:**
        *   Observe the application logs. The `redis.exceptions.ConnectionError: Error 99 connecting to localhost:6379` should no longer appear.
        *   Attempt to trigger email processing (e.g., via the manual poll, if it works, or by waiting for the scheduler) and see if jobs are enqueued and processed (this might require checking RQ dashboard or worker logs if accessible).

### Part 2: Addressing Gunicorn Worker Timeout for `/manual_email_poll` (High Priority)

**Objective:** Prevent the `/manual_email_poll` HTTP route from timing out by making its core operation asynchronous.

**Step 2.1: Modify `/manual_email_poll` Route to Enqueue a Background Task (Recommended)**
    *   **File:** `app/routes.py`
    *   **Change:**
        1.  Modify the `manual_email_poll_route` function.
        2.  Instead of calling `poll_new_emails(current_app._get_current_object())` directly, this route should enqueue a new, dedicated RQ task.
        3.  This new task will then be responsible for calling `poll_new_emails`.
        4.  The route should return an immediate success response to the user (e.g., "Email poll task has been queued.").
    *   **File:** `app/background_tasks.py`
    *   **Change:**
        1.  Define a new simple task, e.g., `def trigger_full_email_poll():`.
        2.  Inside this task, get the current app context and then call the existing `poll_new_emails(current_app)`.
        3.  Ensure this new task is registered with RQ if necessary or uses the existing queue.

    **Example Pseudocode for `app/routes.py`:**
    ```python
    # from app.background_tasks import get_email_queue # Or a general task queue
    # from flask import current_app

    # @app.route('/manual_email_poll', methods=['POST'])
    # @login_required # or other appropriate auth
    # def manual_email_poll_route():
    #     try:
    #         # Get the default RQ queue or a specific one for this kind of task
    #         # This might be 'default' or a new one like 'system_tasks'
    #         task_queue = get_email_queue() # Or a more general queue
    #         task_queue.enqueue('app.background_tasks.trigger_full_email_poll', job_timeout='15m')
    #         flash('Manual email poll has been successfully queued.', 'success')
    #     except Exception as e:
    #         logging.error(f"Error enqueuing manual email poll: {e}")
    #         flash('Error queuing manual email poll. Check logs.', 'danger')
    #     return redirect(url_for('main.dashboard')) # Or wherever appropriate
    ```

    **Example Pseudocode for `app/background_tasks.py` (new task):**
    ```python
    # from flask import current_app
    # # (Ensure get_email_queue, poll_new_emails are available)

    # def trigger_full_email_poll():
    #     """
    #     RQ task to initiate the poll_new_emails function.
    #     Ensures it runs in the background with app context.
    #     """
    #     app = current_app._get_current_object() # Get the actual app instance
    #     with app.app_context():
    #         logging.info("[ManualTrigger] Starting poll_new_emails via RQ task.")
    #         try:
    #             poll_new_emails(app) # Call the existing polling function
    #             logging.info("[ManualTrigger] poll_new_emails task completed.")
    #         except Exception as e:
    #             logging.error(f"[ManualTrigger] Error in trigger_full_email_poll task: {e}", exc_info=True)
    #             # Depending on retry needs, this task could raise the exception
    #             # to let RQ handle retries as configured for the queue.
    ```

**Step 2.2: Alternative - Increase Gunicorn Timeout (Less Recommended)**
    *   **Action:** Modify the Gunicorn startup command to increase the worker timeout.
    *   **Details:** This usually involves changing a `Procfile` (if used by Replit for startup) or the `replit.nix` file if it defines the run command. The Gunicorn parameter is `--timeout <seconds>` (e.g., `--timeout 120` for 2 minutes).
    *   **Drawbacks:** This approach ties up a web worker for the entire duration of the poll, which can be long if many emails are processed. This reduces the server's ability to handle other incoming web requests and is generally not as scalable or robust as offloading to a background task.

**Recommendation:** Strongly recommend **Step 2.1** (making the route asynchronous) as it's a more robust and scalable solution.

### Part 3: Alternative - Evaluate Removing Redis (If Redis Setup Remains Problematic)

**Objective:** Simplify infrastructure by using Postgres for task queuing if Redis cannot be reliably configured or is undesired in the Replit environment.

**Context:** This is a significant architectural change and should be considered if resolving Redis connectivity (Part 1) proves overly complex or if a single database solution is strongly preferred.

**Step 3.1: Detailed Design for Postgres-based Task Queue & Scheduler**
    *   **Task Queue Table (`pending_tasks` in Postgres):**
        *   Schema: `id (PK)`, `task_type (VARCHAR)`, `payload (JSONB)`, `status (VARCHAR: pending, processing, success, failed)`, `created_at (TIMESTAMPZ)`, `scheduled_for (TIMESTAMPZ, optional)`, `processed_at (TIMESTAMPZ, optional)`, `attempts (INTEGER)`, `last_error (TEXT)`.
    *   **Worker Logic (replaces `worker.py`):**
        *   Script that connects to Postgres.
        *   Loop: Polls `pending_tasks` for tasks where `status='pending'` and `scheduled_for <= NOW()` (or is NULL).
        *   Uses `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1` to claim a task, updating its `status` to `processing` and incrementing `attempts`.
        *   Calls a handler function based on `task_type` (e.g., `process_email_task_handler(payload)`).
        *   Updates task `status` to `success` or `failed` (with `last_error`).
        *   Implements retry logic (e.g., exponential backoff by updating `scheduled_for` on failure).
    *   **In-App Scheduler (replaces `scheduler.py` and `rq-scheduler`):**
        *   Use `APScheduler` library, configured with a `SQLAlchemyJobStore` pointing to the Postgres database.
        *   Schedule a recurring job that calls a function (e.g., `trigger_email_polling_job_creation`).
        *   `trigger_email_polling_job_creation`: This function would create a new task in `pending_tasks` with `task_type='poll_all_new_emails'`. The worker would then pick this up and execute the actual `poll_new_emails` logic.

**Step 3.2: Refactor `app/background_tasks.py`**
    *   Remove `get_redis_conn`, `get_email_queue`.
    *   Modify `poll_new_emails`:
        *   It will be called by the new worker when a `'poll_all_new_emails'` task is processed.
        *   Instead of enqueuing `process_email_job` to RQ, it will insert new rows into `pending_tasks` with `task_type='process_single_email'` and the email summary as payload.
    *   The existing `process_email_job` logic would be moved into `process_single_email_task_handler` used by the new worker.

**Step 3.3: Implement New Worker Script**
    *   Create `postgres_worker.py` (or similar) containing the polling and task execution logic described in Step 3.1.
    *   This worker needs to be run as a separate process (e.g., defined in Replit's run command or a Procfile if used).

**Step 3.4: Implement In-App Scheduler with `APScheduler`**
    *   Integrate `APScheduler` into the Flask app initialization (`app/__init__.py`).
    *   Configure it to use `SQLAlchemyJobStore` with the Postgres DB.
    *   Schedule the task that creates `'poll_all_new_emails'` entries in `pending_tasks` table.

**Step 3.5: Refactor `/manual_email_poll` Route in `app/routes.py`**
    *   Instead of enqueuing to RQ, it will insert a task directly into `pending_tasks` table with `task_type='poll_all_new_emails'` and an immediate `scheduled_for` time.

**Step 3.6: Testing**
    *   Thoroughly test the new queue, worker, and scheduler mechanisms.

**Implications:**
*   This removes the Redis dependency entirely.
*   Requires significant development effort to build a robust custom task queue and scheduler.
*   Shifts complexity from infrastructure (Redis setup) to application code.

## 3. Testing and Verification

*   **For Redis Connection (after Step 1.3):**
    *   Monitor application logs closely after restart. Confirm no `redis.exceptions.ConnectionError` related to `localhost:6379` or "Cannot assign requested address."
    *   Verify that emails sent to the monitored mailbox are picked up by the scheduler, enqueued, and processed by workers (check database or application state).

*   **For Gunicorn Timeout (after implementing Step 2.1):**
    *   Trigger the `/manual_email_poll` route multiple times.
    *   **Expected:** The web request should return very quickly with a "queued" message.
    *   **Verification:**
        *   Check application logs to see the `trigger_full_email_poll` task being enqueued.
        *   Check logs from the RQ worker to see it pick up and execute `trigger_full_email_poll`, which then calls `poll_new_emails`.
        *   Ensure no Gunicorn worker timeouts are logged for the `/manual_email_poll` route.
        *   Confirm emails are actually polled and processed as a result of the manually triggered task.

## 4. Update Project Status File

*   Throughout this process, update `project_status.md` to reflect the current state of these fixes. 