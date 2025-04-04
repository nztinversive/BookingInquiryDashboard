import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError

# Import necessary components from the main app package and services
# We assume db and models are accessible via the app context later
# We need the service functions directly
from ms_graph_service import (
    fetch_email_details as ms_fetch_email_details,
    fetch_attachments_list as ms_fetch_attachments_list,
    fetch_new_emails_since as ms_fetch_new_emails_since
)
from data_extraction_service import extract_travel_data

# --- Background Polling Setup ---
POLL_INTERVAL_SECONDS = 120  # Check every 2 minutes
last_checked_timestamp = None # Store the timestamp of the last successful check globally (for simplicity)
stop_polling = threading.Event() # Event to signal thread to stop
polling_thread = None # Global reference to the thread

def process_email_automatically(app, db, Email, ExtractedData, AttachmentMetadata, email_summary):
    """
    Fetches full email, extracts data, and saves to DB using the provided app context.
    Triggered by the background poller for new emails.
    """
    email_graph_id = email_summary.get('id')
    if not email_graph_id:
        logging.warning("Email summary missing ID. Skipping.")
        return

    logging.info(f"[PollingThread] Processing new email automatically: ID {email_graph_id}")

    # --- Operations requiring DB need app context ---
    with app.app_context():
        # Check if already processed
        try:
            existing_email = db.session.get(Email, email_graph_id)
            if existing_email:
                logging.info(f"[PollingThread] Email {email_graph_id} already found in DB (Status: {existing_email.processing_status}). Skipping.")
                return
        except Exception as db_check_err:
             logging.error(f"[PollingThread] Error checking DB for existing email {email_graph_id}: {db_check_err}")
             # Continue processing, maybe create will fail gracefully if duplicate
             pass # Or return if DB check is critical

        processing_status = 'pending' # Initial status
        processing_error_msg = None
        new_email_instance = None

        try:
            # --- Create initial Email record to lock it ---
            # Parse received_at from summary - handle potential errors
            received_dt = None
            received_dt_str = email_summary.get('receivedDateTime')
            try:
                if received_dt_str:
                    if received_dt_str.endswith('Z'):
                        received_dt = datetime.fromisoformat(received_dt_str.replace('Z', '+00:00'))
                    else:
                        received_dt = datetime.fromisoformat(received_dt_str)
            except (ValueError, TypeError) as dt_err:
                logging.warning(f"[PollingThread] Could not parse receivedDateTime '{received_dt_str}' for {email_graph_id}: {dt_err}")

            new_email_instance = Email(
                graph_id=email_graph_id,
                subject=email_summary.get('subject'),
                received_at=received_dt,
                processing_status='processing' # Mark as actively processing
            )
            db.session.add(new_email_instance)
            db.session.commit() # Commit immediately to prevent race conditions
            logging.info(f"[PollingThread] Created initial DB record for email {email_graph_id}")
            # --- ---

            # Fetching/Extraction can happen outside context if they don't need DB/app config directly
            # (Assuming service functions handle their own config e.g., API keys from env vars)

        except IntegrityError as ie:
             logging.warning(f"[PollingThread] DB Integrity error creating initial record for {email_graph_id}: {ie}. Likely duplicate, skipping.")
             db.session.rollback()
             return # Stop processing this email
        except Exception as initial_db_err:
             logging.error(f"[PollingThread] Error creating initial DB record for {email_graph_id}: {initial_db_err}", exc_info=True)
             db.session.rollback()
             return # Stop processing this email


    # --- Fetching and Extraction (Outside initial DB transaction) ---
    try:
        # 1. Fetch full email details (including body)
        logging.info(f"[PollingThread] Fetching full details for {email_graph_id}...")
        email_details = ms_fetch_email_details(email_graph_id)
        if not email_details:
            raise Exception(f"Failed to fetch full details for email {email_graph_id}")

        email_body_html = email_details.get("body", {}).get("content", "")
        sender_info = email_details.get('from', {}).get('emailAddress', {})

        # 2. Extract data
        logging.info(f"[PollingThread] Extracting data for {email_graph_id}...")
        extracted_data_dict, source = extract_travel_data(email_body_html)
        logging.info(f"[PollingThread] Extraction complete for {email_graph_id}. Source: {source}. Data keys: {list(extracted_data_dict.keys())}")

        # 3. Validate data
        essential_fields = ["first_name", "last_name", "travel_start_date", "travel_end_date", "trip_cost"]
        missing_fields_list = [field for field in essential_fields if not extracted_data_dict.get(field)]
        validation_status = "Incomplete" if missing_fields_list else "Complete"
        logging.info(f"[PollingThread] Validation status for {email_graph_id}: {validation_status}. Missing: {missing_fields_list}")

        # 4. Fetch attachments list
        logging.info(f"[PollingThread] Fetching attachments list for {email_graph_id}...")
        attachments_list = ms_fetch_attachments_list(email_graph_id)

        # --- Save Extracted Data and Attachments to DB --- (Requires app_context again)
        with app.app_context():
            # Reload the email instance to ensure it's bound to this session
            email_to_update = db.session.get(Email, email_graph_id)
            if not email_to_update:
                 raise Exception(f"Could not find email {email_graph_id} in DB to update.")

            # Update sender info fetched earlier
            email_to_update.sender_address = sender_info.get('address')
            email_to_update.sender_name = sender_info.get('name')

            # Create ExtractedData record
            new_extracted_data = ExtractedData(
                # email_graph_id is automatically handled by backref
                data=extracted_data_dict, # Store the whole dict as JSONB
                extraction_source=source,
                validation_status=validation_status,
                missing_fields=",".join(missing_fields_list) if missing_fields_list else None
            )
            email_to_update.extracted_data = new_extracted_data # Link via relationship
            logging.debug(f"[PollingThread] Prepared ExtractedData for {email_graph_id}")

            # Create AttachmentMetadata records
            if attachments_list:
                logging.debug(f"[PollingThread] Processing {len(attachments_list)} attachments for {email_graph_id}")
                for att_meta in attachments_list:
                    # Check if attachment already exists (e.g., if reprocessing)
                    existing_att = db.session.get(AttachmentMetadata, att_meta.get('id'))
                    if not existing_att:
                        new_att = AttachmentMetadata(
                            graph_id=att_meta.get('id'),
                            # email_graph_id is handled by backref
                            name=att_meta.get('name'),
                            content_type=att_meta.get('contentType'),
                            size_bytes=att_meta.get('size')
                        )
                        email_to_update.attachments.append(new_att) # Link via relationship
                        logging.debug(f"[PollingThread] Prepared AttachmentMetadata: {att_meta.get('name')}")
                    else:
                         logging.debug(f"[PollingThread] Attachment {att_meta.get('id')} already exists. Skipping.")

            # Update final status
            email_to_update.processing_status = 'processed'
            email_to_update.processed_at = datetime.now(timezone.utc)
            email_to_update.processing_error = None # Clear previous errors

            db.session.commit() # Commit all changes for this email
            logging.info(f"[PollingThread] Successfully processed and saved email {email_graph_id} to database.")
            processing_status = 'processed' # Final status

    except Exception as e:
        processing_status = 'error'
        processing_error_msg = f"Error processing email {email_graph_id} after initial DB record: {e}"
        logging.error(processing_error_msg, exc_info=True)
        # Update status to error in DB
        with app.app_context():
            try:
                error_email = db.session.get(Email, email_graph_id)
                if error_email:
                    error_email.processing_status = 'error'
                    error_email.processing_error = str(processing_error_msg)[:1000] # Limit error length
                    error_email.processed_at = datetime.now(timezone.utc)
                    db.session.commit()
                    logging.info(f"[PollingThread] Updated email {email_graph_id} status to 'error' in DB.")
                else:
                    logging.warning(f"[PollingThread] Could not find email {email_graph_id} to mark as error.")
            except Exception as db_err:
                logging.error(f"[PollingThread] Failed to update email {email_graph_id} status to error in DB: {db_err}")
                db.session.rollback() # Rollback the error update attempt


def poll_new_emails(app, db, Email, ExtractedData, AttachmentMetadata):
    """Fetches recent emails based on timestamp and triggers processing."""
    global last_checked_timestamp
    logging.info("[PollingThread] Polling for new emails...")

    # Ensure last_checked_timestamp is initialized before first use
    if last_checked_timestamp is None:
        # On first run, set timestamp to avoid fetching everything
        last_checked_timestamp = datetime.now(timezone.utc) - timedelta(minutes=10)
        logging.info(f"[PollingThread] First poll run. Setting initial timestamp to: {last_checked_timestamp.isoformat()}")

    try:
        # Use the ensured timestamp for the check
        current_check_time = last_checked_timestamp
        logging.info(f"[PollingThread] Checking for emails received after: {current_check_time.isoformat()}")

        # Get new email summaries
        new_emails = ms_fetch_new_emails_since(current_check_time)

        # Initialize timestamp for this batch; start with the time we are checking *from*
        latest_processed_timestamp = current_check_time

        if new_emails:
            # Process oldest new email first to maintain order
            logging.info(f"[PollingThread] Found {len(new_emails)} new email(s). Processing...")
            for email_summary in new_emails:
                logging.info(f"  - [PollingThread] Queueing Email ID: {email_summary.get('id')}, Subject: {email_summary.get('subject')}, Received: {email_summary.get('receivedDateTime')}")
                # --- Trigger processing ---
                # Pass dependencies explicitly
                process_email_automatically(app, db, Email, ExtractedData, AttachmentMetadata, email_summary)
                # ------------------------

                # Update latest timestamp processed *within this batch*
                try:
                    received_dt_str = email_summary['receivedDateTime']
                    if received_dt_str.endswith('Z'):
                        new_timestamp = datetime.fromisoformat(received_dt_str.replace('Z', '+00:00'))
                    else:
                        new_timestamp = datetime.fromisoformat(received_dt_str)

                    if new_timestamp > latest_processed_timestamp:
                         latest_processed_timestamp = new_timestamp

                except (KeyError, ValueError, TypeError) as ts_err:
                     logging.error(f"[PollingThread] Error parsing timestamp from email {email_summary.get('id')}: {ts_err}. Timestamp will not advance based on this email.")
                     # Don't update timestamp based on this email if parsing failed

            # Only advance the timestamp if a newer email was successfully processed
            if latest_processed_timestamp > last_checked_timestamp:
                last_checked_timestamp = latest_processed_timestamp
                logging.info(f"[PollingThread] Advanced last_checked_timestamp to: {last_checked_timestamp.isoformat()}") # Use INFO level
            else:
                 logging.debug("[PollingThread] No newer emails processed in this batch, timestamp remains unchanged.")


        else:
            logging.info("[PollingThread] No new emails found since last check.")
            # Optionally update the timestamp to 'now' even if none were found
            # last_checked_timestamp = datetime.now(timezone.utc)

    except Exception as e:
        logging.error(f"[PollingThread] Error during email polling: {str(e)}", exc_info=True)

def background_poller(app):
    """Background thread function to periodically poll emails."""
    global last_checked_timestamp
    # Initial timestamp will be set on the first run of poll_new_emails
    last_checked_timestamp = None

    logging.info("Background email polling thread started. Waiting for app context...")

    # Need models and db, but imports might happen late in app factory
    # Wait briefly for app context to be available before first poll attempt
    stop_polling.wait(5) # Wait 5 seconds before first poll

    while not stop_polling.is_set():
        try:
            with app.app_context():
                 # Import models and db instance *inside* context just before use
                 # This handles cases where they are initialized later in app factory
                 from . import db # Assumes db is in app/__init__.py
                 from .models import Email, ExtractedData, AttachmentMetadata

                 if not db:
                      logging.error("[PollingThread] DB instance not available in app context. Skipping poll cycle.")
                 else:
                      poll_new_emails(app, db, Email, ExtractedData, AttachmentMetadata)

        except Exception as e:
            # Log errors occurring outside the main poll_new_emails try-except
            # e.g., issues getting app context or importing db/models
            logging.error(f"[PollingThread] Unhandled exception in background_poller loop: {e}", exc_info=True)

        # Wait for the specified interval or until stop event is set
        wait_time = POLL_INTERVAL_SECONDS
        logging.debug(f"[PollingThread] Polling cycle complete. Waiting for {wait_time} seconds...")
        stop_polling.wait(wait_time)

    logging.info("Background email polling thread stopped.")


def start_background_polling(app):
    """Starts the background polling thread if not already running."""
    global polling_thread
    if polling_thread is None or not polling_thread.is_alive():
        stop_polling.clear() # Ensure stop event is cleared
        polling_thread = threading.Thread(target=background_poller, args=(app,), name="EmailPollingThread", daemon=True)
        polling_thread.start()
        logging.info("Background polling thread initiated.")
    else:
        logging.info("Background polling thread already running.")

def shutdown_background_polling():
     """Signals the background polling thread to stop."""
     global polling_thread
     logging.info("Signaling polling thread to stop...")
     stop_polling.set()
     if polling_thread and polling_thread.is_alive():
         # Optional: Wait briefly for thread to exit, but Gunicorn/server shutdown might handle this
         polling_thread.join(timeout=5)
         if polling_thread.is_alive():
             logging.warning("Polling thread did not exit gracefully within timeout.")
         else:
             logging.info("Polling thread stopped.")
     else:
         logging.info("Polling thread was not running or already stopped.") 