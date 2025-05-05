import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from celery_utils import celery # Import the celery instance
# We need access to the Flask app instance to get context and config
# A common pattern is to create the Flask app within the task or pass necessary config
# Let's try creating the app instance within the task for now.
# This requires the app factory pattern to be robust or careful initialization.
# Alternatively, pass config values directly if app context isn't strictly needed beyond that.

# Import services and potentially models - need to adjust paths
from ms_graph_service import (
    fetch_email_details as ms_fetch_email_details,
    fetch_attachments_list as ms_fetch_attachments_list,
    fetch_new_emails_since as ms_fetch_new_emails_since
)
from data_extraction_service import extract_travel_data, classify_email_intent
# Database models will be needed within the app context
# We'll import db and models dynamically inside the task when app context is available

# Define essential fields here if not imported from elsewhere
essential_fields = ["first_name", "last_name", "travel_start_date", "travel_end_date", "trip_cost"]

# TODO: Define or import NEGATIVE_FILTERS used in the original polling logic.
NEGATIVE_FILTERS = {
    "senders": ["no-reply@", "noreply@", "support@", "mailer-daemon@", "postmaster@", "bounce@", "info@", "newsletter@", "updates@"],
    "subjects": ["undeliverable:", "delivery status notification", "out of office", "automatic reply", "newsletter", "update", "promotion"]
}

# TODO: Improve state management for last_checked_timestamp.
# Using a simple global variable here is NOT suitable for distributed workers.
# Consider storing this in Redis, the database, or another persistent store.
global_last_checked_timestamp = None

@celery.task(bind=True, name='tasks.process_single_email')
def process_single_email(self, email_summary, classified_intent):
    \"\"\"
    Celery task to fetch, process, and save data for a single email.
    Handles app context creation for database operations.
    \"\"\"
    task_id = self.request.id # Get Celery task ID
    email_graph_id = email_summary.get('id')
    if not email_graph_id:
        logging.warning(f"[Task:{task_id}] Email summary missing ID. Skipping.")
        return {'status': 'skipped', 'reason': 'missing_id'}

    logging.info(f"[Task:{task_id}] Starting processing for email ID: {email_graph_id}")

    # Need the Flask app instance for context and db session
    # Import the app factory function from app package
    try:
        from app import create_app
        app = create_app() # Create a new app instance for this task
        # Configure clients if necessary (they might be configured at app creation)
        # from ms_graph_service import configure_ms_graph_client
        # from data_extraction_service import configure_openai_client
        # configure_ms_graph_client(app.config)
        # configure_openai_client(app.config)

    except ImportError:
        logging.error(f"[Task:{task_id}] Could not import or create Flask app. Cannot proceed with DB operations.")
        # Decide on error handling: retry? fail permanently?
        # For now, raise an exception to let Celery handle retries based on its config
        raise Exception("Failed to create Flask app instance for task.")
    except Exception as app_create_err:
        logging.error(f"[Task:{task_id}] Error creating Flask app: {app_create_err}")
        raise Exception(f"Error creating Flask app: {app_create_err}")


    # --- Perform operations that need the app context (like DB checks) ---
    with app.app_context():
        try:
            from app import db # Import db instance now that context is active
            from app.models import Email # Import required models

            existing_email = db.session.get(Email, email_graph_id)
            if existing_email:
                logging.info(f"[Task:{task_id}] Email {email_graph_id} already processed (Status: {existing_email.processing_status}). Skipping.")
                return {'status': 'skipped', 'reason': 'already_processed'}
        except Exception as db_check_err:
             logging.error(f"[Task:{task_id}] Error checking DB for existing email {email_graph_id}: {db_check_err}")
             # Decide if safe to proceed - could lead to duplicate processing attempts. Retry might be safer.
             # Celery's retry mechanisms can be configured for such infrastructure errors.
             # Using default retry behavior for now.
             raise self.retry(exc=db_check_err, countdown=10, max_retries=3) # Example retry

    # --- Fetching and Extraction (Can happen outside DB transaction/app context if services don't depend on it) ---
    # Assuming service functions configure their own clients or are configured globally/via app creation
    email_details = None
    extracted_data_dict = {}
    source = None
    validation_status = "Incomplete"
    missing_fields_list = []
    attachments_list = []
    sender_address = None
    sender_name = None

    try:
        logging.info(f"[Task:{task_id}] Fetching full details for {email_graph_id}...")
        email_details = ms_fetch_email_details(email_graph_id) # Assumes MS Graph client is configured
        if not email_details:
            # Consider retrying network/API errors
            logging.warning(f"[Task:{task_id}] Failed to fetch full details for {email_graph_id}. Maybe retry?")
            raise Exception(f"Failed to fetch email details for {email_graph_id}") # Let Celery handle retry/failure

        email_body_html = email_details.get("body", {}).get("content", "")
        sender_info = email_details.get('from', {}).get('emailAddress', {})
        sender_address = sender_info.get('address')
        sender_name = sender_info.get('name')
        if not sender_address:
            logging.warning(f"[Task:{task_id}] Email {email_graph_id} missing sender address. Processing may be incomplete.")

        logging.info(f"[Task:{task_id}] Extracting data for {email_graph_id}...")
        extracted_data_dict, source = extract_travel_data(email_body_html) # Assumes OpenAI client is configured
        logging.info(f"[Task:{task_id}] Extraction complete. Source: {source}. Keys: {list(extracted_data_dict.keys())}")

        missing_fields_list = [field for field in essential_fields if not extracted_data_dict.get(field)]
        validation_status = "Incomplete" if missing_fields_list else "Complete"
        logging.info(f"[Task:{task_id}] Validation status: {validation_status}. Missing: {missing_fields_list}")

        logging.info(f"[Task:{task_id}] Fetching attachments list for {email_graph_id}...")
        attachments_list = ms_fetch_attachments_list(email_graph_id)

    except Exception as fetch_extract_err:
        logging.error(f"[Task:{task_id}] Error during fetch/extraction for email {email_graph_id}: {fetch_extract_err}", exc_info=True)
        # Decide how to handle this failure. Retry? Mark as permanently failed?
        # For now, let the task fail and rely on Celery's default behavior or future dead-letter config.
        # We might want to save an 'error' status to the DB here if possible.
        # Let's try saving an error status to the DB before failing the task.
        try:
             with app.app_context():
                 from app import db
                 from app.models import Email
                 # Try to find/create email record just to mark error
                 error_email = db.session.get(Email, email_graph_id)
                 if not error_email:
                     error_email = Email(graph_id=email_graph_id, subject=email_summary.get('subject', 'Unknown Subject'), intent=classified_intent, sender_address=sender_address, sender_name=sender_name)
                     db.session.add(error_email)

                 error_email.processing_status = 'fetch_extract_error'
                 error_email.processing_error = f"Fetch/Extract Error: {str(fetch_extract_err)[:1000]}"
                 error_email.processed_at = datetime.now(timezone.utc)
                 db.session.commit()
                 logging.info(f"[Task:{task_id}] Marked email {email_graph_id} with fetch/extract error in DB.")
        except Exception as db_save_err:
             logging.error(f"[Task:{task_id}] Failed to save fetch/extract error status to DB for {email_graph_id}: {db_save_err}")
             # Still raise the original exception to fail the task
        raise fetch_extract_err # Reraise the original exception


    # --- DB Operations: Inquiry finding/creation, Email creation, Data merging ---
    with app.app_context():
        try:
            # Re-import db and models within this context block
            from app import db
            from app.models import Inquiry, Email, ExtractedData, AttachmentMetadata

            # Check again if email was processed concurrently
            existing_email_check = db.session.get(Email, email_graph_id)
            if existing_email_check:
                logging.info(f"[Task:{task_id}] Email {email_graph_id} was created concurrently. Skipping DB operations.")
                return {'status': 'skipped', 'reason': 'concurrent_creation'}

            inquiry = None
            new_email_instance = None
            inquiry_extracted_data = None

            # --- Find or Create Inquiry ---
            if sender_address:
                inquiry = db.session.query(Inquiry).filter_by(primary_email_address=sender_address).first()
                if not inquiry:
                    logging.info(f"[Task:{task_id}] Creating new Inquiry for {sender_address}")
                    inquiry = Inquiry(primary_email_address=sender_address, status='new')
                    db.session.add(inquiry)
                    db.session.flush() # Get inquiry.id
                    logging.info(f"[Task:{task_id}] Created new Inquiry ID {inquiry.id}")
                else:
                    logging.info(f"[Task:{task_id}] Found existing Inquiry ID {inquiry.id}")
            else:
                logging.warning(f"[Task:{task_id}] No sender address for {email_graph_id}. Cannot link to Inquiry.")

            # --- Create Email Record ---
            received_dt = None
            received_dt_str = email_summary.get('receivedDateTime')
            try:
                if received_dt_str:
                    received_dt = datetime.fromisoformat(received_dt_str.replace('Z', '+00:00')) if received_dt_str.endswith('Z') else datetime.fromisoformat(received_dt_str)
            except (ValueError, TypeError) as dt_err:
                logging.warning(f"[Task:{task_id}] Could not parse receivedDateTime '{received_dt_str}': {dt_err}")

            new_email_instance = Email(
                graph_id=email_graph_id,
                subject=email_summary.get('subject'),
                received_at=received_dt,
                processing_status='processing',
                sender_address=sender_address,
                sender_name=sender_name,
                intent=classified_intent,
                celery_task_id=task_id # Store Celery task ID
            )
            if inquiry:
                new_email_instance.inquiry_id = inquiry.id
            db.session.add(new_email_instance)
            logging.info(f"[Task:{task_id}] Prepared Email record. Intent: '{classified_intent}'. Inquiry: {inquiry.id if inquiry else 'No'}")

            # --- Find or Create/Merge ExtractedData ---
            if inquiry:
                inquiry_extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=inquiry.id).first()
                if not inquiry_extracted_data:
                    logging.info(f"[Task:{task_id}] Creating new ExtractedData for Inquiry {inquiry.id}")
                    inquiry_extracted_data = ExtractedData(
                        inquiry_id=inquiry.id,
                        data=extracted_data_dict,
                        extraction_source=source,
                        validation_status=validation_status,
                        missing_fields=",".join(missing_fields_list) if missing_fields_list else None
                    )
                    db.session.add(inquiry_extracted_data)
                else:
                    logging.info(f"[Task:{task_id}] Merging into existing ExtractedData for Inquiry {inquiry.id}")
                    current_data = inquiry_extracted_data.data or {}
                    merged_data = current_data.copy()
                    updated = False
                    for key, value in extracted_data_dict.items():
                        if value and (key not in merged_data or not merged_data[key]):
                            merged_data[key] = value
                            updated = True
                            logging.debug(f"[Task:{task_id}] Merged key '{key}'")

                    if updated:
                        inquiry_extracted_data.data = merged_data
                        merged_missing = [field for field in essential_fields if not merged_data.get(field)]
                        inquiry_extracted_data.validation_status = "Incomplete" if merged_missing else "Complete"
                        inquiry_extracted_data.missing_fields = ",".join(merged_missing) if merged_missing else None
                        inquiry_extracted_data.extraction_source = source
                        logging.info(f"[Task:{task_id}] Merged data updated. New status: {inquiry_extracted_data.validation_status}")

            # --- Create AttachmentMetadata ---
            if attachments_list:
                logging.debug(f"[Task:{task_id}] Processing {len(attachments_list)} attachments")
                for att_meta in attachments_list:
                    existing_att = db.session.get(AttachmentMetadata, att_meta.get('id'))
                    if not existing_att:
                        new_att = AttachmentMetadata(
                            graph_id=att_meta.get('id'),
                            email_graph_id=email_graph_id,
                            name=att_meta.get('name'),
                            content_type=att_meta.get('contentType'),
                            size_bytes=att_meta.get('size')
                        )
                        db.session.add(new_att)

            # --- Finalize Status ---
            new_email_instance.processing_status = 'processed'
            new_email_instance.processed_at = datetime.now(timezone.utc)
            new_email_instance.processing_error = None
            if inquiry:
                inquiry.updated_at = datetime.now(timezone.utc) # Explicitly update Inquiry timestamp

            db.session.commit()
            logging.info(f"[Task:{task_id}] Successfully processed email {email_graph_id}. Inquiry: {inquiry.id if inquiry else 'N/A'}.}")
            return {'status': 'processed', 'inquiry_id': inquiry.id if inquiry else None}

        except IntegrityError as ie:
            db.session.rollback()
            logging.warning(f"[Task:{task_id}] DB Integrity error for {email_graph_id}: {ie}. Likely duplicate race condition. Skipping.")
            return {'status': 'skipped', 'reason': 'db_integrity_error'}
        except Exception as process_err:
            db.session.rollback()
            error_msg = f"DB processing error: {process_err}"
            logging.error(f"[Task:{task_id}] {error_msg}", exc_info=True)

            # Attempt to mark Email/Inquiry as error in a final DB transaction
            try:
                 with app.app_context(): # New context for error update
                    from app import db
                    from app.models import Email, Inquiry

                    email_to_mark_error = db.session.get(Email, email_graph_id)
                    inquiry_id_to_mark_error = None
                    if email_to_mark_error:
                        inquiry_id_to_mark_error = email_to_mark_error.inquiry_id # Get associated inquiry if linked
                        email_to_mark_error.processing_status = 'error'
                        email_to_mark_error.processing_error = str(error_msg)[:1000]
                        email_to_mark_error.processed_at = datetime.now(timezone.utc)
                        logging.info(f"[Task:{task_id}] Attempting to mark Email {email_graph_id} as 'error'...")
                    else:
                        # If email wasn't even added, maybe create a minimal error entry?
                        # Or just log and rely on task failure. For now, just log.
                         logging.warning(f"[Task:{task_id}] Could not find email {email_graph_id} to mark as DB error.")

                    if inquiry_id_to_mark_error:
                        inquiry_to_mark_error = db.session.get(Inquiry, inquiry_id_to_mark_error)
                        if inquiry_to_mark_error:
                             inquiry_to_mark_error.status = 'Error'
                             inquiry_to_mark_error.updated_at = datetime.now(timezone.utc)
                             logging.info(f"[Task:{task_id}] Attempting to mark Inquiry {inquiry_id_to_mark_error} as 'Error'...")

                    db.session.commit()
                    logging.info(f"[Task:{task_id}] Successfully updated error status in DB after processing error.")
            except Exception as db_err_update:
                logging.error(f"[Task:{task_id}] Failed even to update DB status to error for {email_graph_id}: {db_err_update}")

            # Re-raise the exception to mark the task as failed in Celery
            raise self.retry(exc=process_err, countdown=60, max_retries=2) # Retry DB errors less aggressively?


@celery.task(name='tasks.poll_and_dispatch_emails')
def poll_and_dispatch_emails():
    """
    Celery task to poll for new emails and dispatch processing tasks.
    Uses a global variable for timestamp (needs improvement for production).
    """
    global global_last_checked_timestamp
    logging.info("[PollTask] Starting email poll and dispatch...")

    # --- App Context for DB Access (needed for initial timestamp) ---
    try:
        from app import create_app
        app = create_app()
    except Exception as app_create_err:
        logging.error(f"[PollTask] Error creating Flask app: {app_create_err}. Cannot initialize timestamp or poll.")
        # Consider retry or raising error
        return {'status': 'error', 'reason': 'app_creation_failed'}

    # --- Initialize Timestamp ---
    with app.app_context():
        from app import db
        from app.models import Email # Import necessary model
        if global_last_checked_timestamp is None:
            try:
                latest_email_time = db.session.query(func.max(Email.received_at)).scalar()
                if latest_email_time:
                    global_last_checked_timestamp = latest_email_time
                    logging.info(f"[PollTask] Initialized timestamp from DB: {global_last_checked_timestamp.isoformat()}")
                else:
                    global_last_checked_timestamp = datetime.now(timezone.utc) - timedelta(minutes=30)
                    logging.info(f"[PollTask] Initialized timestamp (fallback): {global_last_checked_timestamp.isoformat()}")
            except Exception as init_ts_err:
                logging.error(f"[PollTask] Error getting initial timestamp from DB: {init_ts_err}. Using fallback.")
                global_last_checked_timestamp = datetime.now(timezone.utc) - timedelta(minutes=30)

    # --- Fetch New Emails ---
    try:
        current_check_time = global_last_checked_timestamp
        logging.info(f"[PollTask] Checking for emails received after: {current_check_time.isoformat()}")
        new_emails = ms_fetch_new_emails_since(current_check_time)
    except Exception as fetch_err:
        logging.error(f"[PollTask] Error fetching new emails: {fetch_err}", exc_info=True)
        # Consider retry or specific error handling
        return {'status': 'error', 'reason': 'fetch_failed'}

    # --- Process and Dispatch ---
    latest_processed_timestamp = current_check_time
    dispatched_count = 0
    filtered_count = 0

    if not new_emails:
        logging.info("[PollTask] No new emails found.")
    else:
        logging.info(f"[PollTask] Found {len(new_emails)} new email summaries. Filtering and dispatching...")
        for email_summary in new_emails:
            email_id = email_summary.get('id')
            logging.debug(f"  [PollTask] Checking Email ID: {email_id}")

            should_filter = False
            classified_intent = "unknown"
            filter_reason = ""

            try:
                sender_info = email_summary.get('from', {}).get('emailAddress', {})
                sender_address = (sender_info.get('address') or "").lower()
                subject = (email_summary.get('subject') or "").lower()
                body_preview = (email_summary.get('bodyPreview') or "").lower()

                # 1. Negative Filtering
                for pattern in NEGATIVE_FILTERS["senders"]:
                    if pattern in sender_address:
                        should_filter = True
                        filter_reason = f"Sender matches negative pattern: {pattern}"
                        break
                if not should_filter:
                    for pattern in NEGATIVE_FILTERS["subjects"]:
                        if pattern in subject:
                            should_filter = True
                            filter_reason = f"Subject matches negative pattern: {pattern}"
                            break

                # 2. AI Intent Classification
                if not should_filter:
                    classified_intent = classify_email_intent(subject, body_preview)
                    logging.info(f"  [PollTask] Classified Intent for {email_id}: '{classified_intent}'")
                    if classified_intent != 'inquiry':
                        should_filter = True
                        filter_reason = f"Classified intent is '{classified_intent}', not 'inquiry'"

            except Exception as filter_err:
                logging.warning(f"[PollTask] Error during filtering/classification for {email_id}: {filter_err}. Will dispatch with intent='unknown'.")
                should_filter = False # Process if filtering logic fails
                classified_intent = "unknown"

            if should_filter:
                logging.info(f"  [PollTask] Filtering out Email ID: {email_id}. Reason: {filter_reason}")
                filtered_count += 1
            else:
                # Dispatch the processing task to Celery
                logging.info(f"  [PollTask] Dispatching task process_single_email for Email ID: {email_id} (Intent: '{classified_intent}')")
                try:
                    process_single_email.delay(email_summary, classified_intent)
                    dispatched_count += 1
                except Exception as dispatch_err:
                    logging.error(f"[PollTask] Failed to dispatch task for email {email_id}: {dispatch_err}")
                    # Decide how to handle dispatch failure - maybe retry polling later?

            # --- Timestamp Update Logic (advance past processed/filtered emails) ---
            try:
                current_email_received_dt_str = email_summary.get('receivedDateTime')
                if current_email_received_dt_str:
                    current_email_timestamp = datetime.fromisoformat(current_email_received_dt_str.replace('Z', '+00:00')) if current_email_received_dt_str.endswith('Z') else datetime.fromisoformat(current_email_received_dt_str)
                    if current_email_timestamp > latest_processed_timestamp:
                        latest_processed_timestamp = current_email_timestamp
            except (KeyError, ValueError, TypeError) as ts_err:
                logging.error(f"[PollTask] Error parsing timestamp from {email_id}: {ts_err}. Timestamp might not advance correctly.")

    # --- Final Timestamp Advancement ---
    if latest_processed_timestamp > global_last_checked_timestamp:
        global_last_checked_timestamp = latest_processed_timestamp
        logging.info(f"[PollTask] Advanced global timestamp to: {global_last_checked_timestamp.isoformat()}")
        # TODO: Persist this timestamp reliably (DB, Redis, etc.)
    else:
        logging.debug("[PollTask] Timestamp did not advance.")

    logging.info(f"[PollTask] Poll finished. Dispatched: {dispatched_count}, Filtered: {filtered_count}")
    return {'status': 'complete', 'dispatched': dispatched_count, 'filtered': filtered_count} 