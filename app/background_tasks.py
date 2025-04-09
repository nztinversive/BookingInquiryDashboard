import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

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
    Fetches full email, finds/creates Inquiry, links email, extracts data,
    merges data into Inquiry's ExtractedData, and saves to DB.
    """
    email_graph_id = email_summary.get('id')
    if not email_graph_id:
        logging.warning("Email summary missing ID. Skipping.")
        return

    logging.info(f"[PollingThread] Starting processing for email ID: {email_graph_id}")

    # --- Check if email already exists (outside transaction initially) ---
    # Needs context for DB access
    with app.app_context():
        # Import Inquiry here as it's needed for checks now
        from .models import Inquiry # Import Inquiry model

        try:
            existing_email = db.session.get(Email, email_graph_id)
            if existing_email:
                logging.info(f"[PollingThread] Email {email_graph_id} already found in DB (Status: {existing_email.processing_status}). Skipping.")
                return
        except Exception as db_check_err:
             logging.error(f"[PollingThread] Error checking DB for existing email {email_graph_id}: {db_check_err}")
             # Decide if safe to proceed - could lead to duplicate processing attempts
             # For now, let's risk it and rely on later IntegrityError handling
             pass

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
        logging.info(f"[PollingThread] Fetching full details for {email_graph_id}...")
        email_details = ms_fetch_email_details(email_graph_id)
        if not email_details:
            raise Exception(f"Failed to fetch full details for email {email_graph_id}")

        email_body_html = email_details.get("body", {}).get("content", "")
        sender_info = email_details.get('from', {}).get('emailAddress', {})
        sender_address = sender_info.get('address') # Extract sender address early
        sender_name = sender_info.get('name')
        if not sender_address:
            logging.warning(f"[PollingThread] Email {email_graph_id} missing sender address. Cannot link to Inquiry effectively.")
            # Decide how to handle: Skip? Process without Inquiry link?
            # For now, log and continue, it might fail later when trying to find/create Inquiry.

        # 2. Extract data
        logging.info(f"[PollingThread] Extracting data for {email_graph_id}...")
        extracted_data_dict, source = extract_travel_data(email_body_html)
        logging.info(f"[PollingThread] Extraction complete for {email_graph_id}. Source: {source}. Data keys: {list(extracted_data_dict.keys())}")

        # 3. Validate extracted data
        essential_fields = ["first_name", "last_name", "travel_start_date", "travel_end_date", "trip_cost"]
        missing_fields_list = [field for field in essential_fields if not extracted_data_dict.get(field)]
        validation_status = "Incomplete" if missing_fields_list else "Complete"
        logging.info(f"[PollingThread] Validation status for {email_graph_id}: {validation_status}. Missing: {missing_fields_list}")

        # 4. Fetch attachments list
        logging.info(f"[PollingThread] Fetching attachments list for {email_graph_id}...")
        attachments_list = ms_fetch_attachments_list(email_graph_id)

    except Exception as fetch_extract_err:
        # If fetching or extraction fails *before* DB operations, log and exit.
        # No email record created yet.
        logging.error(f"[PollingThread] Error during fetch/extraction for email {email_graph_id}: {fetch_extract_err}", exc_info=True)
        # Optionally, create an Email record marked as 'fetch_error' or similar if needed.
        # For now, just return.
        return

    # --- DB Operations: Inquiry finding/creation, Email creation, Data merging ---
    with app.app_context():
        # Re-import models within context
        from .models import Inquiry, Email, ExtractedData, AttachmentMetadata

        # Check again if email was created by a concurrent process
        existing_email_check = db.session.get(Email, email_graph_id)
        if existing_email_check:
            logging.info(f"[PollingThread] Email {email_graph_id} was created by another process. Skipping.")
            return

        inquiry = None
        new_email_instance = None
        inquiry_extracted_data = None

        try:
            # --- Find or Create Inquiry ---
            if sender_address:
                inquiry = db.session.query(Inquiry).filter_by(primary_email_address=sender_address).first()
                if inquiry:
                    logging.info(f"[PollingThread] Found existing Inquiry ID {inquiry.id} for sender {sender_address}")
                else:
                    logging.info(f"[PollingThread] No existing Inquiry found for {sender_address}. Creating new one.")
                    inquiry = Inquiry(
                        primary_email_address=sender_address,
                        status='new' # Or derive status based on data/email content later
                    )
                    db.session.add(inquiry)
                    # We need the inquiry ID, flush to get it before creating ExtractedData if needed
                    db.session.flush()
                    logging.info(f"[PollingThread] Created new Inquiry ID {inquiry.id}")
            else:
                # Handle case where sender address was missing
                # Option 1: Fail processing
                # raise ValueError(f"Cannot process email {email_graph_id} without a sender address to link to an Inquiry.")
                # Option 2: Log and skip Inquiry linking (email won't appear on dashboard)
                logging.warning(f"[PollingThread] Skipping Inquiry link for email {email_graph_id} due to missing sender address.")
                # Option 3: Create a placeholder Inquiry (less ideal)
                # For now, we proceed without an inquiry link if address is missing

            # --- Create Email Record ---
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
                processing_status='processing', # Mark as processing
                sender_address=sender_address, # Save sender info
                sender_name=sender_name
                # inquiry_id will be set below if inquiry exists
            )

            if inquiry: # Only link if we found/created an inquiry
                new_email_instance.inquiry_id = inquiry.id

            db.session.add(new_email_instance)
            logging.info(f"[PollingThread] Prepared Email record for {email_graph_id}. Linked to Inquiry: {inquiry.id if inquiry else 'No'}")

            # --- Find or Create/Merge ExtractedData for the Inquiry ---
            if inquiry:
                inquiry_extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=inquiry.id).first()

                if not inquiry_extracted_data:
                    logging.info(f"[PollingThread] No ExtractedData found for Inquiry {inquiry.id}. Creating new one.")
                    inquiry_extracted_data = ExtractedData(
                        inquiry_id=inquiry.id,
                        data=extracted_data_dict, # Start with data from this email
                extraction_source=source,
                validation_status=validation_status,
                missing_fields=",".join(missing_fields_list) if missing_fields_list else None
            )
                    # Link via relationship automatically happens if `backref` is set correctly
                    # inquiry.extracted_data = inquiry_extracted_data # Or explicitly set
                    db.session.add(inquiry_extracted_data)
                else:
                    logging.info(f"[PollingThread] Found existing ExtractedData for Inquiry {inquiry.id}. Merging data.")
                    # --- Data Merging Logic ---
                    # Simple strategy: Fill empty fields in existing data with new data
                    current_data = inquiry_extracted_data.data or {}
                    merged_data = current_data.copy() # Start with existing data
                    updated = False
                    for key, value in extracted_data_dict.items():
                        # Only update if the new value is not empty AND
                        # (the key doesn't exist in merged_data OR the existing value is empty/None)
                        if value and (key not in merged_data or not merged_data[key]):
                            merged_data[key] = value
                            updated = True
                            logging.debug(f"[PollingThread] Merged/Updated key '{key}' for Inquiry {inquiry.id}")

                    if updated:
                        inquiry_extracted_data.data = merged_data
                        # Re-validate based on merged data
                        merged_missing = [field for field in essential_fields if not merged_data.get(field)]
                        inquiry_extracted_data.validation_status = "Incomplete" if merged_missing else "Complete"
                        inquiry_extracted_data.missing_fields = ",".join(merged_missing) if merged_missing else None
                        # extraction_source could potentially list multiple sources over time? Or just the latest?
                        inquiry_extracted_data.extraction_source = source # Overwrite with latest source for now
                        logging.info(f"[PollingThread] Merged data updated for Inquiry {inquiry.id}. New status: {inquiry_extracted_data.validation_status}")
                    else:
                        logging.info(f"[PollingThread] No new data merged into existing ExtractedData for Inquiry {inquiry.id}.")

            # --- Create AttachmentMetadata records ---
            if attachments_list:
                logging.debug(f"[PollingThread] Processing {len(attachments_list)} attachments for {email_graph_id}")
                for att_meta in attachments_list:
                    # Check if attachment already exists (should be rare if email check passed)
                    existing_att = db.session.get(AttachmentMetadata, att_meta.get('id'))
                    if not existing_att:
                        new_att = AttachmentMetadata(
                            graph_id=att_meta.get('id'),
                            email_graph_id=email_graph_id, # Explicitly set FK
                            name=att_meta.get('name'),
                            content_type=att_meta.get('contentType'),
                            size_bytes=att_meta.get('size')
                        )
                        db.session.add(new_att) # Add directly to session
                        # Relationship linking happens via FK email_graph_id
                        logging.debug(f"[PollingThread] Prepared AttachmentMetadata: {att_meta.get('name')}")
                    else:
                         logging.debug(f"[PollingThread] Attachment {att_meta.get('id')} already exists. Skipping.")


            # --- Finalize Email Status and Inquiry Timestamp ---
            new_email_instance.processing_status = 'processed'
            new_email_instance.processed_at = datetime.now(timezone.utc)
            new_email_instance.processing_error = None # Clear previous errors

            if inquiry:
                # Trigger Inquiry's updated_at timestamp by modifying it (SQLAlchemy detects change)
                # Alternatively, explicitly set: inquiry.updated_at = datetime.now(timezone.utc)
                # Forcing an update by changing status slightly if needed, or just rely on session flush
                inquiry.status = inquiry.status # No-op change to maybe trigger onupdate? Safer to rely on merged data changes.
                # If ExtractedData was modified, its onupdate should trigger.
                # If only Email was added, we might need to explicitly update Inquiry timestamp:
                inquiry.updated_at = datetime.now(timezone.utc) # Explicitly update timestamp

            db.session.commit() # Commit all changes for this email and inquiry
            logging.info(f"[PollingThread] Successfully processed email {email_graph_id} and updated Inquiry {inquiry.id if inquiry else 'N/A'}.")

        except IntegrityError as ie:
            db.session.rollback()
            logging.warning(f"[PollingThread] DB Integrity error during processing for {email_graph_id}: {ie}. Likely duplicate race condition, skipping.")
            # Don't update status, let the other process handle it.
        except Exception as process_err:
            db.session.rollback()
            error_msg = f"Error processing email {email_graph_id} within DB transaction: {process_err}"
            logging.error(f"[PollingThread] {error_msg}", exc_info=True)
            
            # Capture inquiry ID before trying the error update transaction
            inquiry_id_to_mark_error = inquiry.id if inquiry else None
            
            # Update Email and Inquiry status to 'error' in a separate transaction
            try:
                # Need a fresh session/context potentially, or retry within a new block
                with app.app_context(): # New context for error update
                    email_to_mark_error = db.session.get(Email, email_graph_id)
                    inquiry_to_mark_error = None
                    
                    if email_to_mark_error:
                         # If the email record itself failed to commit earlier, this might still fail
                         email_to_mark_error.processing_status = 'error'
                         email_to_mark_error.processing_error = str(error_msg)[:1000] # Limit error length
                         email_to_mark_error.processed_at = datetime.now(timezone.utc)
                         logging.info(f"[PollingThread] Attempting to mark Email {email_graph_id} as 'error'...")
                    else:
                         # This case happens if the initial Email creation failed and was rolled back.
                         logging.warning(f"[PollingThread] Could not find email {email_graph_id} to mark as error (it might not have been committed).")
                         
                    # Attempt to mark Inquiry as error too
                    if inquiry_id_to_mark_error:
                        inquiry_to_mark_error = db.session.get(Inquiry, inquiry_id_to_mark_error)
                        if inquiry_to_mark_error:
                             inquiry_to_mark_error.status = 'Error' # Set Inquiry status
                             logging.info(f"[PollingThread] Attempting to mark Inquiry {inquiry_id_to_mark_error} as 'Error'...")
                        else:
                             logging.warning(f"[PollingThread] Could not find Inquiry {inquiry_id_to_mark_error} to mark as Error.")

                    # Commit changes after potential modification
                    # Only commit if we found and tried to update at least one record
                    if email_to_mark_error or inquiry_to_mark_error:
                        db.session.commit()
                        logging.info(f"[PollingThread] Successfully updated error status for Email: {email_graph_id} / Inquiry: {inquiry_id_to_mark_error}")
                    else:
                         logging.info("[PollingThread] No records found to update error status.")

            except Exception as db_err_update:
                logging.error(f"[PollingThread] Failed even to update email {email_graph_id} status to error in DB: {db_err_update}")
                # Rollback might not be needed if commit failed


def poll_new_emails(app, db, Email, ExtractedData, AttachmentMetadata, Inquiry):
    """Fetches recent emails based on timestamp and triggers processing."""
    global last_checked_timestamp
    logging.info("[PollingThread] Polling for new emails...")

    # Ensure last_checked_timestamp is initialized before first use
    if last_checked_timestamp is None:
        # On first run, set timestamp to avoid fetching everything
        # Query the latest received_at from existing emails as a better starting point?
        try:
            latest_email_time = db.session.query(func.max(Email.received_at)).scalar()
            if latest_email_time:
                last_checked_timestamp = latest_email_time
                logging.info(f"[PollingThread] Initialized last_checked_timestamp from latest email in DB: {last_checked_timestamp.isoformat()}")
            else:
                # Fallback if no emails in DB
                last_checked_timestamp = datetime.now(timezone.utc) - timedelta(minutes=30) # Check last 30 mins first time
                logging.info(f"[PollingThread] No emails in DB. Setting initial timestamp to: {last_checked_timestamp.isoformat()}")
        except Exception as init_ts_err:
            logging.error(f"[PollingThread] Error getting latest email time for initial timestamp: {init_ts_err}. Falling back.")
            last_checked_timestamp = datetime.now(timezone.utc) - timedelta(minutes=30)
            logging.info(f"[PollingThread] Fallback initial timestamp: {last_checked_timestamp.isoformat()}")


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
            processed_count = 0
            filtered_count = 0
            for email_summary in new_emails:
                logging.info(f"  - [PollingThread] Checking Email ID: {email_summary.get('id')}, Subject: {email_summary.get('subject')}, Received: {email_summary.get('receivedDateTime')}")

                # --- Filtering Logic --- 
                should_filter = False
                is_likely_inquiry = False # Flag for positive match
                filter_reason = ""
                
                # Define Keywords (can be moved to config later)
                NEGATIVE_FILTERS = {
                    "senders": ["@linkedin.com", "@ringcentral.com", 
                                "@promomail.microsoft.com", "@libertyutilities.com", "@calendly.com"],
                    "subjects": ["linkedin", "undeliverable", "out of office", "automatic reply", 
                                 "ringcentral", "incoming fax", "voicemail message",
                                 "urgent:", "expertise in", "learn how", 
                                 "online bill is now available", "new event:",
                                 "new voice message from wireless caller"]
                }
                POSITIVE_KEYWORDS = ["quote", "request", "trip", "travel", "inquiry", "booking", 
                                       "itinerary", "pricing", "cost", "availability", "destination",
                                       # Added based on examples/discussion
                                       "cruise", "insurance", "address", "dates", "payment", "information"]

                try:
                    # Get sender, subject, and body preview for filtering (case-insensitive)
                    sender_info = email_summary.get('from', {}).get('emailAddress', {})
                    sender_address = (sender_info.get('address') or "").lower()
                    subject = (email_summary.get('subject') or "").lower()
                    body_preview = (email_summary.get('bodyPreview') or "").lower()
                    
                    # --- Add Detailed Logging for Debugging ---
                    logging.debug(f"  - [PollingThread] Filter Check - ID: {email_summary.get('id')}")
                    logging.debug(f"      Sender Address: '{sender_address}'")
                    logging.debug(f"      Subject: '{subject}'")
                    logging.debug(f"      Body Preview: '{body_preview[:100]}...'") # Log first 100 chars
                    # -------------------------------------------

                    # 1. Negative Filtering (Check if it should be immediately ignored)
                    for pattern in NEGATIVE_FILTERS["senders"]:
                        if pattern in sender_address:
                            should_filter = True
                            filter_reason = f"Sender matches negative pattern: {pattern}"
                            break # No need to check further negative rules
                    
                    if not should_filter:
                        for pattern in NEGATIVE_FILTERS["subjects"]:
                            if pattern in subject:
                                should_filter = True
                                filter_reason = f"Subject matches negative pattern: {pattern}"
                                break
                    
                    # 2. Positive Filtering (Only if it didn't match negative filters)
                    if not should_filter:
                        is_likely_inquiry = False # Reset flag
                        # Check subject OR body preview for positive keywords
                        text_to_check = subject + " " + body_preview # Combine for easier checking
                        
                        for keyword in POSITIVE_KEYWORDS: # Use the combined list
                            if keyword in text_to_check:
                                is_likely_inquiry = True
                                logging.debug(f"  - [PollingThread] Positive keyword '{keyword}' found for Email ID: {email_summary.get('id')}")
                                break # Found at least one positive keyword
                        
                        # If no positive keywords found in subject or body preview, filter it out
                        if not is_likely_inquiry:
                            should_filter = True
                            filter_reason = "Subject or body preview does not contain positive inquiry keywords"

                except Exception as filter_err:
                    logging.warning(f"[PollingThread] Error during filtering check for email {email_summary.get('id')}: {filter_err}. Processing will proceed.")
                    should_filter = False # Default to processing if filter logic errors

                if should_filter:
                    # Use a different log level (e.g., INFO or DEBUG) for filtered non-inquiries vs definite spam
                    if filter_reason == "Subject or body preview does not contain positive inquiry keywords":
                         logging.debug(f"  - [PollingThread] Skipping email ID: {email_summary.get('id')}. Reason: {filter_reason}")
                    else:
                         logging.info(f"  - [PollingThread] Filtering out Email ID: {email_summary.get('id')}. Reason: {filter_reason}")
                    filtered_count += 1
                    continue # Skip to the next email summary
                # --- End Filtering Logic ---

                logging.info(f"  - [PollingThread] Email ID: {email_summary.get('id')} passed filters. Proceeding to process.")
                # --- Trigger processing ---
                # Pass dependencies explicitly, including Inquiry
                process_email_automatically(app, db, Email, ExtractedData, AttachmentMetadata, email_summary)
                processed_count += 1
                # ------------------------

                # --- Timestamp Update Logic (Moved Inside Loop) ---
                # Always update latest_processed_timestamp to the timestamp of the *current* email being checked
                # This ensures we advance past filtered emails
                try:
                    current_email_received_dt_str = email_summary['receivedDateTime']
                    if current_email_received_dt_str.endswith('Z'):
                        current_email_timestamp = datetime.fromisoformat(current_email_received_dt_str.replace('Z', '+00:00'))
                    else:
                        current_email_timestamp = datetime.fromisoformat(current_email_received_dt_str)

                    # Update the high water mark for this batch
                    if current_email_timestamp > latest_processed_timestamp:
                        latest_processed_timestamp = current_email_timestamp
                        logging.debug(f"  - [PollingThread] Updated latest_processed_timestamp for batch to: {latest_processed_timestamp.isoformat()}")

                except (KeyError, ValueError, TypeError) as ts_err:
                     logging.error(f"[PollingThread] Error parsing timestamp from email {email_summary.get('id')}: {ts_err}. Timestamp for batch might not advance correctly.")
                     # Don't update timestamp based on this email if parsing failed, but allow loop to continue
                # --- End Timestamp Update Logic ---

            # --- Final Timestamp Advancement ---
            # After checking all emails in the batch, advance the global timestamp 
            # if the latest checked email timestamp is newer than the starting timestamp.
            if latest_processed_timestamp > last_checked_timestamp:
                last_checked_timestamp = latest_processed_timestamp
                logging.info(f"[PollingThread] Advanced global last_checked_timestamp to: {last_checked_timestamp.isoformat()}")
            else:
                logging.debug("[PollingThread] No emails checked in this batch had a timestamp newer than the last global check. Timestamp remains unchanged.")
            # --------------------------------
            
            logging.info(f"[PollingThread] Batch complete. Processed: {processed_count}, Filtered: {filtered_count}")


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
    stop_polling.wait(10) # Wait 10 seconds before first poll (increased slightly)

    while not stop_polling.is_set():
        try:
            with app.app_context():
                 # Import models and db instance *inside* context just before use
                 # This handles cases where they are initialized later in app factory
                 from . import db # Assumes db is in app/__init__.py
                 # Import all required models for poll_new_emails
                 from .models import Email, ExtractedData, AttachmentMetadata, Inquiry

                 if not db:
                      logging.error("[PollingThread] DB instance not available in app context. Skipping poll cycle.")
                 else:
                      # Pass Inquiry to poll_new_emails
                      poll_new_emails(app, db, Email, ExtractedData, AttachmentMetadata, Inquiry)

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