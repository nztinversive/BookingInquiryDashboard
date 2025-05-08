import logging
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func
from flask import current_app # Import current_app

# RQ Imports
from redis import Redis
from rq import Queue

# Import necessary components from the main app package and services
# We assume db and models are accessible via the app context later
# We need the service functions directly
from ms_graph_service import (
    fetch_email_details as ms_fetch_email_details,
    fetch_attachments_list as ms_fetch_attachments_list,
    fetch_new_emails_since as ms_fetch_new_emails_since
)
from data_extraction_service import extract_travel_data, classify_email_intent

# --- RQ Setup ---
# Get Redis URL from Flask app config
# Note: This top-level code runs when the module is imported.
# We need an app context to access config here, which might be tricky.
# Better approach: Initialize queue inside functions or pass config.
# Let's try initializing lazily or within functions.
redis_conn = None
email_queue = None

def get_redis_conn():
    """Gets a Redis connection using config from the current app context."""
    global redis_conn
    if redis_conn is None:
        redis_url = current_app.config.get('REDIS_URL', 'redis://localhost:6379/0')
        redis_conn = Redis.from_url(redis_url)
    return redis_conn

def get_email_queue():
    """Gets the RQ email queue instance, initializing connection if needed."""
    global email_queue
    if email_queue is None:
        conn = get_redis_conn()
        email_queue = Queue('email_processing', connection=conn)
    return email_queue

# --- Background Polling Setup ---
# Remove hardcoded interval, it will be read from config where needed
# POLL_INTERVAL_SECONDS = 120 
last_checked_timestamp = None 

# Refactored function to be an RQ job
# Removed app, db, Email, ExtractedData, AttachmentMetadata args
def process_email_job(email_summary, classified_intent):
    """
    RQ Job: Fetches full email, finds/creates Inquiry, links email, extracts data,
    merges data into Inquiry's ExtractedData, and saves to DB.
    Also saves the pre-classified intent.

    Args:
        email_summary (dict): Dictionary containing email metadata from MS Graph.
        classified_intent (str): The pre-classified intent of the email.
    """
    # Get app instance - requires job to be run in an environment where app is available
    # This might need adjustment depending on worker setup (e.g., passing app factory)
    app = current_app 
    
    # Check if essential data is present
    email_graph_id = email_summary.get('id')
    if not email_graph_id:
        logging.warning("Email summary missing ID in job data. Skipping.")
        return {"status": "error", "message": "Missing email ID"} # RQ jobs can return results

    logging.info(f"[RQ Job {email_graph_id}] Starting processing...")

    # --- Fetching and Extraction (Can happen outside DB transaction) ---
    email_details = None
    extracted_data_dict = {}
    source = None
    validation_status = "Incomplete"
    missing_fields_list = []
    attachments_list = []
    sender_address = None
    sender_name = None

    try:
        # 1. Fetch full email details (including body and sender)
        logging.info(f"[RQ Job {email_graph_id}] Fetching full details...")
        # Assume ms_fetch_email_details handles necessary auth/config from app context or global config
        email_details = ms_fetch_email_details(email_graph_id)
        if not email_details:
            raise Exception(f"Failed to fetch full details for email {email_graph_id}")

        email_body_html = email_details.get("body", {}).get("content", "")
        sender_info = email_details.get('from', {}).get('emailAddress', {})
        sender_address = sender_info.get('address') 
        sender_name = sender_info.get('name')
        if not sender_address:
            logging.warning(f"[RQ Job {email_graph_id}] Email missing sender address. Cannot link to Inquiry effectively.")

        # 2. Extract data
        logging.info(f"[RQ Job {email_graph_id}] Extracting data...")
        # Assume extract_travel_data handles necessary auth/config
        extracted_data_dict, source = extract_travel_data(email_body_html)
        logging.info(f"[RQ Job {email_graph_id}] Extraction complete. Source: {source}. Data keys: {list(extracted_data_dict.keys())}")

        # 3. Validate extracted data
        essential_fields = ["first_name", "last_name", "travel_start_date", "travel_end_date", "trip_cost"] # Define locally or import from config/models
        missing_fields_list = [field for field in essential_fields if not extracted_data_dict.get(field)]
        validation_status = "Incomplete" if missing_fields_list else "Complete"
        logging.info(f"[RQ Job {email_graph_id}] Validation status: {validation_status}. Missing: {missing_fields_list}")

        # 4. Fetch attachments list
        logging.info(f"[RQ Job {email_graph_id}] Fetching attachments list...")
        # Assume ms_fetch_attachments_list handles necessary auth/config
        attachments_list = ms_fetch_attachments_list(email_graph_id)

    except Exception as fetch_extract_err:
        logging.error(f"[RQ Job {email_graph_id}] Error during fetch/extraction: {fetch_extract_err}", exc_info=True)
        # Log error and potentially mark email as failed later if possible
        # For now, return error status
        return {"status": "error", "message": f"Fetch/Extraction failed: {fetch_extract_err}"}


    # --- DB Operations: Inquiry finding/creation, Email creation, Data merging ---
    # Use app context for DB operations
    with app.app_context():
        # Import necessary components within context
        from . import db # Get db from the app context
        from .models import Inquiry, Email, ExtractedData, AttachmentMetadata # Import models

        # Check if email was created by a concurrent process or previous failed run
        existing_email_check = db.session.get(Email, email_graph_id)
        if existing_email_check:
            logging.info(f"[RQ Job {email_graph_id}] Email already exists in DB (Status: {existing_email_check.processing_status}). Skipping DB operations.")
            # Decide if we should return success or a specific status
            return {"status": "skipped", "message": "Email already processed"} 

        inquiry = None
        new_email_instance = None
        inquiry_extracted_data = None
        commit_success = False

        try:
            # --- Find or Create Inquiry ---
            if sender_address:
                inquiry = db.session.query(Inquiry).filter_by(primary_email_address=sender_address).first()
                if inquiry:
                    logging.info(f"[RQ Job {email_graph_id}] Found existing Inquiry ID {inquiry.id} for sender {sender_address}")
                else:
                    logging.info(f"[RQ Job {email_graph_id}] No existing Inquiry for {sender_address}. Creating new one.")
                    inquiry = Inquiry(
                        primary_email_address=sender_address,
                        status='new' # Default status
                    )
                    db.session.add(inquiry)
                    db.session.flush() # Get inquiry.id
                    logging.info(f"[RQ Job {email_graph_id}] Created new Inquiry ID {inquiry.id}")
            else:
                 logging.warning(f"[RQ Job {email_graph_id}] Skipping Inquiry link due to missing sender address.")

            # --- Create Email Record ---
            received_dt = None
            received_dt_str = email_summary.get('receivedDateTime')
            try:
                if received_dt_str:
                    # Handle timezone offset correctly
                        received_dt = datetime.fromisoformat(received_dt_str.replace('Z', '+00:00'))
            except (ValueError, TypeError) as dt_err:
                logging.warning(f"[RQ Job {email_graph_id}] Could not parse receivedDateTime '{received_dt_str}': {dt_err}")

            new_email_instance = Email(
                graph_id=email_graph_id,
                subject=email_summary.get('subject'),
                received_at=received_dt,
                processing_status='processing', # Start as processing
                sender_address=sender_address,
                sender_name=sender_name,
                intent=classified_intent
                # inquiry_id set below
            )

            if inquiry: 
                new_email_instance.inquiry_id = inquiry.id

            db.session.add(new_email_instance)
            logging.info(f"[RQ Job {email_graph_id}] Prepared Email record. Intent: '{classified_intent}'. Linked to Inquiry: {inquiry.id if inquiry else 'No'}")

            # --- Find or Create/Merge ExtractedData for the Inquiry ---
            if inquiry:
                inquiry_extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=inquiry.id).first()

                if not inquiry_extracted_data:
                    logging.info(f"[RQ Job {email_graph_id}] Creating new ExtractedData for Inquiry {inquiry.id}.")
                    inquiry_extracted_data = ExtractedData(
                        inquiry_id=inquiry.id,
                        data=extracted_data_dict,
                        extraction_source=source,
                        validation_status=validation_status,
                        missing_fields=",".join(missing_fields_list) if missing_fields_list else None
                    )
                    db.session.add(inquiry_extracted_data)
                else:
                    logging.info(f"[RQ Job {email_graph_id}] Found existing ExtractedData for Inquiry {inquiry.id}. Merging.")
                    # --- Data Merging Logic ---
                    current_data = inquiry_extracted_data.data or {}
                    merged_data = current_data.copy() 
                    updated = False
                    for key, value in extracted_data_dict.items():
                        if value and (key not in merged_data or not merged_data[key]):
                            merged_data[key] = value
                            updated = True

                    if updated:
                        inquiry_extracted_data.data = merged_data
                        merged_missing = [field for field in essential_fields if not merged_data.get(field)]
                        inquiry_extracted_data.validation_status = "Incomplete" if merged_missing else "Complete"
                        inquiry_extracted_data.missing_fields = ",".join(merged_missing) if merged_missing else None
                        inquiry_extracted_data.extraction_source = source # Update source
                        logging.info(f"[RQ Job {email_graph_id}] Merged data updated for Inquiry {inquiry.id}. New status: {inquiry_extracted_data.validation_status}")
                    else:
                        logging.info(f"[RQ Job {email_graph_id}] No new data merged for Inquiry {inquiry.id}.")

            # --- Create AttachmentMetadata records ---
            if attachments_list:
                logging.debug(f"[RQ Job {email_graph_id}] Processing {len(attachments_list)} attachments.")
                for att_meta in attachments_list:
                    att_graph_id = att_meta.get('id')
                    if not att_graph_id: 
                        logging.warning(f"[RQ Job {email_graph_id}] Attachment missing ID. Skipping.")
                        continue
                    
                    existing_att = db.session.get(AttachmentMetadata, att_graph_id)
                    if not existing_att:
                        new_att = AttachmentMetadata(
                            graph_id=att_graph_id,
                            email_graph_id=email_graph_id, 
                            name=att_meta.get('name'),
                            content_type=att_meta.get('contentType'),
                            size_bytes=att_meta.get('size')
                        )
                        db.session.add(new_att)
                        logging.debug(f"[RQ Job {email_graph_id}] Prepared AttachmentMetadata: {att_meta.get('name')}")
                    # else: log if needed

            # --- Finalize Email Status and Inquiry Timestamp ---
            new_email_instance.processing_status = 'processed'
            new_email_instance.processed_at = datetime.now(timezone.utc)
            new_email_instance.processing_error = None 

            if inquiry:
                # Explicitly update Inquiry timestamp when email is processed
                inquiry.updated_at = datetime.now(timezone.utc) 

            db.session.commit() # Commit all changes
            commit_success = True
            logging.info(f"[RQ Job {email_graph_id}] Successfully processed and committed to DB.")
            return {"status": "success", "inquiry_id": inquiry.id if inquiry else None}

        except IntegrityError as ie:
            db.session.rollback()
            logging.error(f"[RQ Job {email_graph_id}] Database integrity error (likely duplicate email/attachment ID): {ie}", exc_info=True)
            # Check if the email record was the cause
            existing_email_final = db.session.get(Email, email_graph_id)
            if existing_email_final:
                 return {"status": "skipped", "message": f"Duplicate entry detected (IntegrityError): {ie}"}
            else:
                 # Mark email as permanently failed if possible, or just log
                 return {"status": "error", "message": f"IntegrityError, email not found after rollback: {ie}"}
        except Exception as db_err:
            db.session.rollback()
            logging.error(f"[RQ Job {email_graph_id}] Unhandled database error: {db_err}", exc_info=True)
            # Attempt to mark the email as failed in a new session if the instance exists
            try:
                if new_email_instance and new_email_instance.graph_id: # Ensure we have an ID
                    # Need a way to update status outside failed transaction
                    # Maybe enqueue a separate 'mark_failed' job?
                    # Or try a minimal update session (less safe)
                    logging.warning(f"[RQ Job {email_graph_id}] Rolling back DB changes due to error.")
                    # For now, just return error
                    return {"status": "error", "message": f"DB Operation failed: {db_err}"}

            except Exception as log_err: # Error during error handling
                 logging.error(f"[RQ Job {email_graph_id}] Error during error handling/logging for DB exception: {log_err}")
                 return {"status": "error", "message": f"DB Operation failed, error handling also failed: {db_err}"}
        finally:
            # Ensure session is closed, though app_context should handle this
            # db.session.remove() # Usually handled by context manager
            pass

    # --- Final check for return ---
    # This part might be technically unreachable if commit_success=True due to earlier returns
    # Kept for clarity, but can be simplified/removed if desired.
    if commit_success:
         return {"status": "success", "inquiry_id": inquiry.id if inquiry else None}
    else:  # <--- Make sure this 'else' lines up with the 'if commit_success:'
         # This path is hit if an error occurred before commit (handled above)
         # or if somehow the commit failed silently (unlikely)
         logging.error(f"[RQ Job {email_graph_id}] Reached end of job function without successful commit.")
         return {"status": "error", "message": "Processing finished unexpectedly without success or specific error."}

# Modified function to enqueue jobs instead of processing directly
# Signature changed: Removed db, Email, ExtractedData, AttachmentMetadata, Inquiry arguments
def poll_new_emails(app):
    """
    Checks for new emails since the last check, classifies them, and enqueues
    them for processing.

    This function itself might be run periodically (e.g., by a scheduler or simple loop).
    It now only requires the app context for logging/config, not for DB operations directly.
    """
    global last_checked_timestamp
    from ms_graph_service import fetch_new_emails_since
    from data_extraction_service import classify_email_intent

    # Get queue instance (ensures connection uses app config)
    current_email_queue = get_email_queue()

    with app.app_context():
        logging.info("[EmailPoller] Starting poll cycle.")
        # Determine the timestamp for fetching emails
        if last_checked_timestamp is None:
            # On first run, check for emails from the last hour (or configure differently)
            since_timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
            logging.info("[EmailPoller] First run: checking emails since 1 hour ago.")
        else:
            since_timestamp = last_checked_timestamp
            logging.info(f"[EmailPoller] Checking emails since {since_timestamp.isoformat()}")

        current_check_time = datetime.now(timezone.utc) # Timestamp before fetching

        try:
            new_email_summaries = fetch_new_emails_since(since_timestamp.isoformat())

            if not new_email_summaries:
                logging.info("[EmailPoller] No new emails found.")
            else:
                logging.info(f"[EmailPoller] Found {len(new_email_summaries)} new email(s). Classifying and enqueueing...")
                processed_count = 0
                for email_summary in new_email_summaries:
                    email_graph_id = email_summary.get('id')
                    email_subject = email_summary.get('subject', '')
                    email_snippet = email_summary.get('bodyPreview', '')
                    if not email_graph_id:
                        logging.warning("[EmailPoller] Skipping email summary with no ID.")
                        continue

                    try:
                        # --- Classify Intent ---
                        classified_intent = classify_email_intent(email_subject, email_snippet)
                        logging.info(f"[EmailPoller] Classified intent for {email_graph_id}: '{classified_intent}'")

                        # --- Enqueue for processing ---
                        job = current_email_queue.enqueue(
                            'app.background_tasks.process_email_job',
                            email_summary,
                            classified_intent,
                            job_timeout='10m',
                            result_ttl=86400
                        )
                        logging.info(f"[EmailPoller] Enqueued job {job.id} for email {email_graph_id}.")
                        processed_count += 1

                    except Exception as enqueue_err:
                        logging.error(f"[EmailPoller] Failed to classify or enqueue email {email_graph_id}: {enqueue_err}", exc_info=True)
                        # Decide if this email should be skipped permanently or retried later

                logging.info(f"[EmailPoller] Finished enqueueing {processed_count} emails.")

            # Update timestamp only after a successful poll (even if no emails found)
            last_checked_timestamp = current_check_time
            # Read interval from config for logging
            poll_interval = app.config.get('POLL_INTERVAL_SECONDS', 120)
            logging.info(f"[EmailPoller] Poll cycle complete. Next check will be after {last_checked_timestamp.isoformat()} (Interval: {poll_interval}s)")

        except Exception as poll_err:
            logging.error(f"[EmailPoller] Error during email polling cycle: {poll_err}", exc_info=True)
            # Do not update last_checked_timestamp on error, so we retry from the same point

# --- Remove Old Threading Functions ---
# The background_poller, start_background_polling, and shutdown_background_polling
# functions that managed the threading.Thread are no longer needed.
# (The actual function definitions below this comment block will be removed)

# def background_poller(app):
#     """The actual function run by the background thread."""
#     logging.info("Background email poller thread starting.")
#     # Import models needed by poll_new_emails (passed as args now)
#     # from .models import Email, ExtractedData, AttachmentMetadata, Inquiry
#     # from . import db

#     while not stop_polling.is_set():
#         try:
#             # Pass necessary components to poll_new_emails
#             # Need app context to access db and models if not passed explicitly
#             with app.app_context():
#                  # Re-import models inside context if needed, or rely on app factory pattern
#                  from .models import Email, ExtractedData, AttachmentMetadata, Inquiry
#                  from . import db
#                  poll_new_emails(app, db, Email, ExtractedData, AttachmentMetadata, Inquiry)
#         except Exception as e:
#             logging.error(f"Error in background polling loop: {e}", exc_info=True)
#             # Avoid spamming logs if there's a persistent error
#             time.sleep(60) # Sleep longer on error
        
#         # Wait for the poll interval or until stop event is set
#         stop_polling.wait(POLL_INTERVAL_SECONDS)
    
#     logging.info("Background email poller thread stopped.")

# def start_background_polling(app):
#     """Starts the background email polling thread."""
#     global polling_thread
#     if polling_thread is None or not polling_thread.is_alive():
#         stop_polling.clear()
#         # Pass the app instance to the thread target function
#         polling_thread = threading.Thread(target=background_poller, args=(app,), daemon=True)
#         polling_thread.start()
#         logging.info("Background email polling thread started.")
#     else:
#         logging.info("Background email polling thread already running.")

# def shutdown_background_polling():
#     """Signals the background polling thread to stop."""
#     global polling_thread
#     if polling_thread and polling_thread.is_alive():
#         logging.info("Signalling background email poller thread to stop...")
#         stop_polling.set()
#         # Optional: Wait for the thread to finish
#         # polling_thread.join(timeout=POLL_INTERVAL_SECONDS + 10) 
#         # if polling_thread.is_alive():
#         #     logging.warning("Polling thread did not stop gracefully.")
#         # polling_thread = None
#     else:
#         logging.info("Background email poller thread not running or already stopped.") 