import logging
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func
from flask import current_app

# Removed RQ Imports:
# from redis import Redis
# from rq import Queue
# import redis

# Import necessary components from the main app package and services
# We assume db and models are accessible via the app context later
# We need the service functions directly
from ms_graph_service import (
    fetch_email_details as ms_fetch_email_details,
    fetch_attachments_list as ms_fetch_attachments_list,
    fetch_new_emails_since as ms_fetch_new_emails_since
)
from data_extraction_service import extract_travel_data, classify_email_intent

# --- Removed RQ Setup ---
# redis_conn = None
# email_queue = None
# def get_redis_conn(): ...
# def get_email_queue(): ...

# --- Global variable for polling timestamp ---
# This might be better managed via a database table/setting in a more robust system
last_checked_timestamp = None

def handle_process_single_email(task_payload):
    """
    Processes a single email. This function contains the core logic previously
    in 'process_email_job'. It's called by the new Postgres-based worker.

    Args:
        task_payload (dict): A dictionary containing 'email_summary' and 'classified_intent'.
                             Example: {"email_summary": {...}, "classified_intent": "..."}
    """
    app = current_app._get_current_object() # Ensure we have the app object for context
    with app.app_context():
        from . import db # Get db from the app context
        from .models import Inquiry, Email, ExtractedData, AttachmentMetadata # Import models

        email_summary = task_payload.get('email_summary')
        classified_intent = task_payload.get('classified_intent')

        if not email_summary or not classified_intent:
            logging.error(f"[TaskHandler] Invalid payload for handle_process_single_email: {task_payload}")
            raise ValueError("Payload missing email_summary or classified_intent")

        email_graph_id = email_summary.get('id')
        if not email_graph_id:
            logging.warning(f"[TaskHandler] Email summary missing ID in task payload for email processing. Skipping.")
            # This specific email processing fails, but the task itself ran.
            # The worker should mark the task as failed with this reason.
            raise ValueError("Email summary missing ID")

        log_prefix = f"[TaskHandler Email: {email_graph_id}]"
        logging.info(f"{log_prefix} Starting processing...")

        # --- Fetching and Extraction (Can happen outside DB transaction for some parts) ---
        email_details = None
        extracted_data_dict = {}
        source = None
        validation_status = "Incomplete"
        missing_fields_list = []
        attachments_list = []
        sender_address = None
        sender_name = None

        try:
            logging.info(f"{log_prefix} Fetching full details...")
            email_details = ms_fetch_email_details(email_graph_id)
            if not email_details:
                raise Exception(f"Failed to fetch full details for email {email_graph_id}")

            email_body_html = email_details.get("body", {}).get("content", "")
            sender_info = email_details.get('from', {}).get('emailAddress', {})
            sender_address = sender_info.get('address')
            sender_name = sender_info.get('name')
            if not sender_address:
                logging.warning(f"{log_prefix} Email missing sender address. Cannot link to Inquiry effectively.")

            logging.info(f"{log_prefix} Extracting data...")
            extracted_data_dict, source = extract_travel_data(email_body_html)
            logging.info(f"{log_prefix} Extraction complete. Source: {source}. Data keys: {list(extracted_data_dict.keys())}")

            essential_fields = app.config.get("ESSENTIAL_EXTRACTION_FIELDS", ["first_name", "last_name", "travel_start_date", "travel_end_date", "trip_cost"])
            missing_fields_list = [field for field in essential_fields if not extracted_data_dict.get(field)]
            validation_status = "Incomplete" if missing_fields_list else "Complete"
            logging.info(f"{log_prefix} Validation status: {validation_status}. Missing: {missing_fields_list}")

            logging.info(f"{log_prefix} Fetching attachments list...")
            attachments_list = ms_fetch_attachments_list(email_graph_id)

        except Exception as fetch_extract_err:
            logging.error(f"{log_prefix} Error during fetch/extraction: {fetch_extract_err}", exc_info=True)
            # Re-raise to be caught by the worker's error handling for the task
            raise

        # --- DB Operations: Inquiry finding/creation, Email creation, Data merging ---
        # Ensure this part is idempotent or handles retries gracefully if the task is re-run
        existing_email_check = db.session.get(Email, email_graph_id)
        if existing_email_check:
            logging.info(f"{log_prefix} Email already exists in DB (Status: {existing_email_check.processing_status}). Assuming already processed or being processed.")
            # Consider what to do here. If status is 'failed', maybe allow reprocessing?
            # For now, if it exists, we skip to avoid IntegrityError.
            # The worker should mark the task successful as this specific instance is handled.
            return {"status": "skipped", "message": "Email already processed or processing"}

        inquiry = None
        new_email_instance = None # Defined here for broader scope in error handling
        
        try:
            if sender_address:
                inquiry = db.session.query(Inquiry).filter_by(primary_email_address=sender_address).first()
                if inquiry:
                    logging.info(f"{log_prefix} Found existing Inquiry ID {inquiry.id} for sender {sender_address}")
                else:
                    logging.info(f"{log_prefix} No existing Inquiry for {sender_address}. Creating new one.")
                    inquiry = Inquiry(primary_email_address=sender_address, status='new')
                    db.session.add(inquiry)
                    db.session.flush() # Get inquiry.id
                    logging.info(f"{log_prefix} Created new Inquiry ID {inquiry.id}")
            else:
                logging.warning(f"{log_prefix} Skipping Inquiry link due to missing sender address.")

            received_dt = None
            received_dt_str = email_summary.get('receivedDateTime')
            try:
                if received_dt_str:
                    received_dt = datetime.fromisoformat(received_dt_str.replace('Z', '+00:00'))
            except (ValueError, TypeError) as dt_err:
                logging.warning(f"{log_prefix} Could not parse receivedDateTime '{received_dt_str}': {dt_err}")

            new_email_instance = Email(
                graph_id=email_graph_id,
                subject=email_summary.get('subject'),
                received_at=received_dt,
                processing_status='processing', # Initial status
                sender_address=sender_address,
                sender_name=sender_name,
                intent=classified_intent,
                inquiry_id=inquiry.id if inquiry else None
            )
            db.session.add(new_email_instance)
            logging.info(f"{log_prefix} Prepared Email record. Intent: '{classified_intent}'. Linked to Inquiry: {inquiry.id if inquiry else 'No'}")

            if inquiry:
                inquiry_extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=inquiry.id).first()
                if not inquiry_extracted_data:
                    logging.info(f"{log_prefix} Creating new ExtractedData for Inquiry {inquiry.id}.")
                    inquiry_extracted_data = ExtractedData(
                        inquiry_id=inquiry.id,
                        data=extracted_data_dict,
                        extraction_source=source,
                        validation_status=validation_status,
                        missing_fields=",".join(missing_fields_list) if missing_fields_list else None
                    )
                    db.session.add(inquiry_extracted_data)
                else:
                    logging.info(f"{log_prefix} Found existing ExtractedData for Inquiry {inquiry.id}. Merging.")
                    current_data = inquiry_extracted_data.data or {}
                    merged_data = current_data.copy()
                    updated = False
                    for key, value in extracted_data_dict.items():
                        if value and (key not in merged_data or not merged_data[key]): # Merge if new or if old was empty
                            merged_data[key] = value
                            updated = True
                    if updated:
                        inquiry_extracted_data.data = merged_data
                        merged_missing = [field for field in essential_fields if not merged_data.get(field)]
                        inquiry_extracted_data.validation_status = "Incomplete" if merged_missing else "Complete"
                        inquiry_extracted_data.missing_fields = ",".join(merged_missing) if merged_missing else None
                        inquiry_extracted_data.extraction_source = source # Update source if data merged
                        logging.info(f"{log_prefix} Merged data updated for Inquiry {inquiry.id}. New status: {inquiry_extracted_data.validation_status}")
                    else:
                        logging.info(f"{log_prefix} No new data merged for Inquiry {inquiry.id}.")
            
            if attachments_list:
                logging.debug(f"{log_prefix} Processing {len(attachments_list)} attachments.")
                for att_meta in attachments_list:
                    att_graph_id = att_meta.get('id')
                    if not att_graph_id:
                        logging.warning(f"{log_prefix} Attachment missing ID. Skipping.")
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
                        logging.debug(f"{log_prefix} Prepared AttachmentMetadata: {att_meta.get('name')}")

            new_email_instance.processing_status = 'processed'
            new_email_instance.processed_at = datetime.now(timezone.utc)
            new_email_instance.processing_error = None
            if inquiry:
                inquiry.updated_at = datetime.now(timezone.utc)

            db.session.commit()
            logging.info(f"{log_prefix} Successfully processed and committed to DB.")
            return {"status": "success", "inquiry_id": inquiry.id if inquiry else None}

        except IntegrityError as ie:
            db.session.rollback()
            logging.error(f"{log_prefix} Database integrity error: {ie}", exc_info=True)
            # Check if the email record exists, if so, it's a duplicate scenario.
            # If it was another IntegrityError (e.g. attachment ID), the new_email_instance might exist
            # and should be marked as failed if possible or the task should be marked failed.
            if db.session.get(Email, email_graph_id):
                 logging.warning(f"{log_prefix} IntegrityError likely due to duplicate email ID. Marking as skipped.")
                 # If email exists, this specific attempt can be considered 'handled' to avoid retry loops on duplicates.
                 # The worker will then mark the task as successful or skipped.
                 return {"status": "skipped", "message": f"Duplicate entry detected (IntegrityError): {ie}"}
            else:
                # If email record wasn't the duplicate, then it's a more complex DB issue.
                if new_email_instance: # if email object was created
                    new_email_instance.processing_status = 'failed'
                    new_email_instance.processing_error = f"IntegrityError: {str(ie)[:1000]}" # Truncate error
                    try:
                        # Try to add and commit just the failed email status
                        db.session.add(new_email_instance) # Re-add if rolled back
                        db.session.commit()
                    except Exception as final_commit_err:
                        db.session.rollback()
                        logging.error(f"{log_prefix} Could not even commit failed status after IntegrityError: {final_commit_err}")
                raise # Re-raise for the worker to mark the task as failed
        
        except Exception as db_err:
            db.session.rollback()
            logging.error(f"{log_prefix} Unhandled database error: {db_err}", exc_info=True)
            if new_email_instance and new_email_instance.graph_id: # if email object was created with an ID
                # Attempt to mark the email as failed in the DB if it was created
                try:
                    # Fetch fresh instance if it was committed before error or use the current one
                    email_to_fail = db.session.get(Email, new_email_instance.graph_id)
                    if not email_to_fail: # If not committed or rolled back
                        email_to_fail = new_email_instance
                        db.session.add(email_to_fail) # Add it back if it wasn't there
                    
                    email_to_fail.processing_status = 'failed'
                    email_to_fail.processing_error = f"DB Error: {str(db_err)[:1000]}" # Truncate error
                    email_to_fail.processed_at = datetime.now(timezone.utc)
                    db.session.commit()
                    logging.info(f"{log_prefix} Marked email as 'failed' in DB due to unhandled error.")
                except Exception as log_err:
                    db.session.rollback()
                    logging.error(f"{log_prefix} Error during error handling for DB exception: Could not mark email as failed. {log_err}")
            raise # Re-raise for the worker to mark the task as failed


def poll_new_emails(app_instance):
    """
    Checks for new emails since the last check, classifies them, and creates
    PendingTask entries for them to be processed by the Postgres-based worker.
    This function is intended to be called by a scheduled task (e.g., from APScheduler via a PendingTask).
    Args:
        app_instance: The Flask app instance.
    """
    global last_checked_timestamp
    # Note: db and PendingTask model are imported within app_context
    
    with app_instance.app_context():
        from . import db
        from .models import PendingTask

        logging.info("[EmailPoller] Starting poll cycle (Postgres Task Queuing).")
        if last_checked_timestamp is None:
            # On first run, check for emails from the last 7 days (or a configurable period)
            # Consider storing this in DB (e.g., a 'settings' table or oldest PendingTask check time)
            # For now, using timedelta similar to before.
            default_catchup_days = app_instance.config.get('INITIAL_POLL_CATCHUP_DAYS', 7)
            since_timestamp = datetime.now(timezone.utc) - timedelta(days=default_catchup_days)
            logging.info(f"[EmailPoller] First run or missing timestamp: checking emails since {default_catchup_days} days ago.")
        else:
            since_timestamp = last_checked_timestamp
            logging.info(f"[EmailPoller] Checking emails since {since_timestamp.isoformat()}")
        
        current_check_time = datetime.now(timezone.utc) # Timestamp before fetching

        try:
            new_email_summaries = ms_fetch_new_emails_since(since_timestamp)

            if not new_email_summaries:
                logging.info("[EmailPoller] No new emails found.")
            else:
                logging.info(f"[EmailPoller] Found {len(new_email_summaries)} new email(s). Classifying and creating tasks...")
                created_task_count = 0
                for email_summary in new_email_summaries:
                    email_graph_id = email_summary.get('id')
                    email_subject = email_summary.get('subject', '')
                    email_snippet = email_summary.get('bodyPreview', '')
                    if not email_graph_id:
                        logging.warning("[EmailPoller] Skipping email summary with no ID.")
                        continue

                    # Classify intent
                    classified_intent = "Unknown Intent" # Default intent
                    try:
                        classified_intent = classify_email_intent(email_subject, email_snippet)
                        logging.info(f"[EmailPoller] Classified intent for {email_graph_id}: '{classified_intent}'")
                    except Exception as classify_err:
                        logging.error(f"[EmailPoller] Failed to classify intent for {email_graph_id}: {classify_err}. Using default intent: '{classified_intent}'", exc_info=True)
                        # classified_intent is already set to default, so we just log and proceed.

                    # Create PendingTask
                    task_payload = {
                        "email_summary": email_summary,
                        "classified_intent": classified_intent
                    }
                    new_pending_task = PendingTask(
                        task_type='process_single_email',
                        payload=task_payload,
                        status='pending',
                        scheduled_for=datetime.now(timezone.utc) # Process ASAP
                    )
                    try:
                        db.session.add(new_pending_task)
                        db.session.commit() # Commit each task individually
                        logging.info(f"[EmailPoller] Created PendingTask for email {email_graph_id}.")
                        created_task_count += 1
                    except Exception as db_task_err:
                        db.session.rollback()
                        logging.error(f"[EmailPoller] Failed to create PendingTask for email {email_graph_id}: {db_task_err}", exc_info=True)
                        # If one task creation fails, we continue to the next email.
                        # The overall poll cycle will still update `last_checked_timestamp` if it doesn't hard crash.

                logging.info(f"[EmailPoller] Finished creating {created_task_count} PendingTasks.")

            # Update timestamp only after a successful poll cycle (even if no emails/tasks created)
            last_checked_timestamp = current_check_time
            poll_interval = app_instance.config.get('POLL_INTERVAL_SECONDS', 300) # Default to 5 mins for Postgres tasks
            logging.info(f"[EmailPoller] Poll cycle complete. Next check will be based on schedule (current time: {current_check_time.isoformat()}, interval: {poll_interval}s).")

        except Exception as poll_err:
            logging.error(f"[EmailPoller] Error during email polling cycle: {poll_err}", exc_info=True)
            # Do not update last_checked_timestamp on error, so the APScheduler task will retry from the same point next time.
            # This function should re-raise the error if it's called from the Postgres worker via a 'poll_all_new_emails' task,
            # so the worker can mark that specific polling task as failed.
            raise


# --- Old RQ-specific job processing function (to be removed or adapted) ---
# def process_email_job(email_summary, classified_intent): ... (this logic is now in handle_process_single_email)

# --- Old threading functions (should be removed as APScheduler will handle scheduling) ---
# def background_poller(app): ...
# def start_background_polling(app): ...
# def shutdown_background_polling(): ...

# Placeholder for a task that triggers polling, to be called by APScheduler
def trigger_email_polling_task_creation():
    """
    Scheduled job to create a 'poll_all_new_emails' task in the PendingTask table.
    This function needs to establish its own Flask app context to interact with the database
    when run by APScheduler.
    """
    # Import the app factory function.
    # This assumes create_app is in app/__init__.py, which is standard.
    from app import create_app 

    # Create a new Flask app instance specifically for this job.
    # This ensures that the job has a valid application context.
    # Note: If create_app() is not idempotent for certain initializations (e.g., re-adding scheduler jobs
    # without 'replace_existing=True', or re-initializing extensions in a conflicting way),
    # this might have side effects. However, it's a common pattern for providing context
    # to background jobs when not using an extension like Flask-APScheduler.
    job_app = create_app()

    if not job_app:
        logging.error("[SchedulerCallback] Failed to create Flask app instance via create_app() for trigger_email_polling_task_creation. Task creation will be skipped.")
        return

    with job_app.app_context():
        # Now that we are within an app context, we can safely import and use app-bound extensions.
        from . import db  # Imports db associated with job_app
        from .models import PendingTask # Imports models related to job_app's SQLAlchemy instance

        logging.info("[SchedulerCallback] APScheduler triggered: Creating a 'poll_all_new_emails' task.")
        try:
            new_task = PendingTask(task_type='poll_all_new_emails', payload={})
            db.session.add(new_task)
            db.session.commit()
            logging.info(f"[SchedulerCallback] Successfully created PendingTask ID {new_task.id} for email polling.")
        except IntegrityError as e:
            db.session.rollback()
            logging.error(f"[SchedulerCallback] IntegrityError when creating polling task: {e}. This might indicate an issue with task uniqueness or DB connection.", exc_info=True)
        except Exception as e:
            db.session.rollback()
            logging.error(f"[SchedulerCallback] Failed to create 'poll_all_new_emails' task due to: {e}", exc_info=True)

# New function to handle WhatsApp messages:
def handle_new_whatsapp_message(payload, app_for_context_param):
    """
    Processes an incoming WhatsApp message payload from Green API.
    This function is called by the task dispatcher (`handle_task`).

    Args:
        payload (dict): The JSON payload of the WhatsApp message from Green API.
        app_for_context_param: The Flask app instance for context.
    """
    # Use the passed app_for_context_param to establish context
    # app = app_for_context_param # Original thought
    # Correct way to use app context for background tasks if not already in one:
    app = current_app._get_current_object() if current_app else app_for_context_param
    
    with app.app_context():
        from . import db
        from .models import Inquiry, WhatsAppMessage, ExtractedData, User # Assuming User might be needed for 'updated_by'

        log_prefix = "[WhatsAppHandler]"
        logging.info(f"{log_prefix} Starting processing of new WhatsApp message.")
        logging.debug(f"{log_prefix} Received payload: {payload}")

        # Extract primary identifiers from payload (adjust keys based on actual Green API structure)
        # These are examples based on common Green API payload structures.
        # Refer to Green API documentation for the exact field names.
        # Example: data might be nested under 'messageData', 'senderData', etc.
        
        # Assuming the payload is the direct webhook data from Green API.
        # Example structure from Green API docs for incoming message:
        # {
        #   "typeWebhook": "incomingMessageReceived",
        #   "instanceData": { "idInstance": ..., "wid": ..., "typeInstance": ... },
        #   "timestamp": ..., 
        #   "idMessage": "...",
        #   "senderData": { "chatId": "[email protected]", "sender": "[email protected]", "senderName": "..." },
        #   "messageData": {
        #     "typeMessage": "textMessage", 
        #     "textMessageData": { "textMessage": "Hello" }
        #     OR "extendedTextMessageData": { "text": "Hello with quote", "stanzaId": ...}
        #     OR "imageMessageData": { "url": ..., "caption": ..., "mimeType": ...}
        #     ...
        #   }
        # }

        id_message = payload.get('idMessage')
        sender_data = payload.get('senderData', {})
        chat_id = sender_data.get('chatId') # e.g., "[email protected]"
        sender = sender_data.get('sender')   # Often same as chatId for user messages
        sender_name = sender_data.get('senderName')

        message_data = payload.get('messageData', {})
        type_message = message_data.get('typeMessage')
        
        text_message = None
        media_url = None
        media_mime_type = None
        media_caption = None
        media_filename = None # Green API might provide this

        if type_message == 'textMessage' and message_data.get('textMessageData'):
            text_message = message_data['textMessageData'].get('textMessage')
        elif type_message == 'extendedTextMessage' and message_data.get('extendedTextMessageData'):
            text_message = message_data['extendedTextMessageData'].get('text') # Often contains quoted msg text too
        elif type_message in ['imageMessage', 'videoMessage', 'documentMessage', 'audioMessage']:
            # Simplified: Extract common media fields. Adjust per specific typeXXXMessageData structure.
            media_data_key = type_message + "Data" # e.g., imageMessageData
            if message_data.get(media_data_key):
                # Common fields (might vary by media type in Green API docs)
                # Prefer 'downloadUrl' if available and directly accessible, otherwise 'url'.
                media_url = message_data[media_data_key].get('downloadUrl') or message_data[media_data_key].get('url')
                media_mime_type = message_data[media_data_key].get('mimeType')
                media_caption = message_data[media_data_key].get('caption')
                media_filename = message_data[media_data_key].get('fileName') # If provided
                if not text_message and media_caption: # Use caption as text if no primary text
                    text_message = media_caption
        # Add more elif for other types like locationMessage, contactMessage, etc. if needed

        wa_timestamp_from_payload = payload.get('timestamp') # This is usually a Unix timestamp (seconds)
        wa_datetime = None
        if wa_timestamp_from_payload:
            try:
                wa_datetime = datetime.fromtimestamp(int(wa_timestamp_from_payload), tz=timezone.utc)
            except (ValueError, TypeError) as e:
                logging.warning(f"{log_prefix} Could not parse Green API timestamp '{wa_timestamp_from_payload}': {e}")

        from_me = False # For incoming, this should generally be False.
                        # GreenAPI's `sender` vs `instanceData.wid` can determine if it's an echo of an outgoing msg.
        if payload.get("instanceData", {}).get("wid") == sender:
            # This logic might need refinement based on how GreenAPI handles echoes of outgoing messages vs. true incoming.
            # Typically, webhooks are for *incoming* messages from others.
            # If 'senderData.sender' is the API's own WID, it's an echo of an outgoing message.
            logging.info(f"{log_prefix} Message sender {sender} matches instance WID. Likely an echo of an outgoing message. Setting from_me=True.")
            from_me = True
            # Decide if you want to process echoes of your own messages. For now, we will.

        if not id_message or not chat_id or not sender:
            logging.error(f"{log_prefix} Essential fields missing from payload: idMessage, chatId, or sender. Payload: {payload}")
            raise ValueError("Essential WhatsApp message identifiers missing in payload")

        # --- Inquiry lookup/creation --- 
        # Use chat_id for inquiry association, as sender might be a group participant if in a group context.
        # For 1-on-1 chats, chat_id and sender (user's WID) are often the same.
        inquiry_identifier_email = f"whatsapp_{chat_id}@internal.placeholder"
        inquiry = None
        
        try:
            inquiry = db.session.query(Inquiry).filter_by(primary_email_address=inquiry_identifier_email).first()
            new_inquiry_created = False
            if not inquiry:
                logging.info(f"{log_prefix} No existing Inquiry for identifier {inquiry_identifier_email}. Creating new one.")
                inquiry = Inquiry(
                    primary_email_address=inquiry_identifier_email,
                    status='new_whatsapp' # Initial status for new WhatsApp inquiries
                )
                db.session.add(inquiry)
                db.session.flush() # Get inquiry.id before linking WhatsAppMessage
                new_inquiry_created = True
                logging.info(f"{log_prefix} Created new Inquiry ID {inquiry.id}")
            else:
                logging.info(f"{log_prefix} Found existing Inquiry ID {inquiry.id} for identifier {inquiry_identifier_email}")
                # Optionally update inquiry status if it was, e.g., 'Complete' and now gets a new message
                if inquiry.status not in ['new_whatsapp', 'Processing', 'Incomplete']: # Example: don't overwrite these
                    pass # Or set to 'new_whatsapp' / 'Follow-up'
                inquiry.updated_at = datetime.now(timezone.utc) # Manually trigger update if not auto by ORM event

            # --- Create WhatsAppMessage record ---
            # Check if this specific message ID already exists for this inquiry to prevent duplicates
            existing_wa_message = db.session.query(WhatsAppMessage).filter_by(id=id_message).first()
            if existing_wa_message:
                logging.warning(f"{log_prefix} WhatsApp message with ID {id_message} already exists. Skipping creation. Status: {existing_wa_message.message_type}")
                # This specific message is a duplicate, but the task for PendingTask can be marked successful.
                return {"status": "skipped", "message": "WhatsApp message already processed (duplicate ID)"}

            new_wa_message = WhatsAppMessage(
                id=id_message, # From Green API
                inquiry_id=inquiry.id,
                wa_chat_id=chat_id,
                sender_number=sender, # This is the sender's WID (e.g., [email protected])
                from_me=from_me,
                message_type=type_message or 'unknown',
                body=text_message,
                media_url=media_url,
                media_mime_type=media_mime_type,
                media_caption=media_caption,
                media_filename=media_filename,
                wa_timestamp=wa_datetime, # Timestamp from Green API when message was sent/received by WA server
                # received_at is defaulted to now() by the model when we save.
                # raw_payload=payload # Optional: store the full JSON if needed for debugging or reprocessing
            )
            db.session.add(new_wa_message)
            logging.info(f"{log_prefix} Prepared WhatsAppMessage record for ID {id_message}. Linked to Inquiry ID {inquiry.id}.")

            # --- Data Extraction (only if there's text content) ---
            extracted_data_dict = None
            extraction_source = None
            current_validation_status = inquiry.status # Preserve current inquiry status before potential update

            if text_message:
                logging.info(f"{log_prefix} Attempting data extraction from text: \"{text_message[:100]}...\"")
                try:
                    extracted_data_dict, extraction_source = extract_travel_data(text_message) # Assuming text input
                    logging.info(f"{log_prefix} Extraction complete. Source: {extraction_source}. Data keys: {list(extracted_data_dict.keys() if extracted_data_dict else [])}")
                except Exception as extract_err:
                    logging.error(f"{log_prefix} Error during data extraction: {extract_err}", exc_info=True)
                    # Proceed without extracted data, but log the error.
                    # The inquiry status might remain 'new_whatsapp' or 'Incomplete'.
            else:
                logging.info(f"{log_prefix} No text message content for data extraction (Type: {type_message}).")

            # --- Upsert ExtractedData & Update Inquiry Status ---
            if extracted_data_dict:
                essential_fields = app.config.get("ESSENTIAL_EXTRACTION_FIELDS", ["first_name", "last_name", "travel_start_date", "travel_end_date", "trip_cost"])
                inquiry_extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=inquiry.id).first()
                if not inquiry_extracted_data:
                    logging.info(f"{log_prefix} Creating new ExtractedData for Inquiry {inquiry.id}.")
                    missing_fields_list = [field for field in essential_fields if not extracted_data_dict.get(field)]
                    current_validation_status = "Incomplete" if missing_fields_list else "Complete"
                    inquiry_extracted_data = ExtractedData(
                        inquiry_id=inquiry.id,
                        data=extracted_data_dict,
                        extraction_source=extraction_source or 'whatsapp_initial_extraction',
                        validation_status=current_validation_status,
                        missing_fields=",".join(missing_fields_list) if missing_fields_list else None
                    )
                    db.session.add(inquiry_extracted_data)
                else:
                    logging.info(f"{log_prefix} Found existing ExtractedData for Inquiry {inquiry.id}. Merging.")
                    current_db_data = inquiry_extracted_data.data or {}
                    merged_data = current_db_data.copy()
                    updated = False
                    for key, value in extracted_data_dict.items():
                        if value and (key not in merged_data or not merged_data[key]): # Merge if new or if old was empty
                            merged_data[key] = value
                            updated = True
                    if updated:
                        inquiry_extracted_data.data = merged_data
                        # Re-validate after merge
                        merged_missing = [field for field in essential_fields if not merged_data.get(field)]
                        current_validation_status = "Incomplete" if merged_missing else "Complete"
                        inquiry_extracted_data.missing_fields = ",".join(merged_missing) if merged_missing else None
                        inquiry_extracted_data.extraction_source = extraction_source or 'whatsapp_merged_extraction' # Update source
                        logging.info(f"{log_prefix} Merged data updated. New validation status for ExtractedData: {current_validation_status}")
                    else:
                        logging.info(f"{log_prefix} No new data merged into ExtractedData for Inquiry {inquiry.id}.")
                        # Use existing validation status if no new data merged
                        current_validation_status = inquiry_extracted_data.validation_status 
                
                # Update Inquiry status based on extraction results
                inquiry.status = current_validation_status
                logging.info(f"{log_prefix} Updated Inquiry {inquiry.id} status to '{inquiry.status}' based on extraction.")
            elif new_inquiry_created: # No data extracted, but it's a new inquiry from WhatsApp
                inquiry.status = 'new_whatsapp' # Remains 'new_whatsapp' or could be 'Incomplete' if preferred
                logging.info(f"{log_prefix} No data extracted. Inquiry {inquiry.id} status remains/set to '{inquiry.status}'.")
            # If not new_inquiry_created and no data extracted, inquiry status remains as it was before this message.

            db.session.commit()
            logging.info(f"{log_prefix} Successfully processed WhatsApp message ID {id_message} and committed to DB.")
            return {"status": "success", "inquiry_id": inquiry.id, "whatsapp_message_id": new_wa_message.id}

        except IntegrityError as ie:
            db.session.rollback()
            # Check if it's a duplicate WhatsAppMessage ID error
            if "whatsapp_messages_pkey" in str(ie).lower() or (id_message and db.session.query(WhatsAppMessage).get(id_message)):
                logging.warning(f"{log_prefix} IntegrityError likely due to duplicate WhatsApp Message ID {id_message}. Marking as skipped. Error: {ie}")
                return {"status": "skipped", "message": f"Duplicate WhatsApp message ID {id_message}"}
            else:
                logging.error(f"{log_prefix} Database integrity error during WhatsApp processing: {ie}", exc_info=True)
                raise # Re-raise for the main task handler to mark PendingTask as failed
        except Exception as e:
            db.session.rollback()
            logging.error(f"{log_prefix} Unhandled error processing WhatsApp message: {e}", exc_info=True)
            raise # Re-raise for the main task handler


# Task handler dispatcher for the new worker
# The worker will call this function with the task_type and payload.
def handle_task(task_type, payload, app_for_context):
    """
    Dispatcher for different task types from the PendingTask queue.
    Args:
        task_type (str): The type of task to handle.
        payload (dict): The payload for the task.
        app_for_context: The Flask application instance for establishing context.
    """
    logging.info(f"[TaskDispatcher] Received task: {task_type}")
    # Ensure we are operating within the provided app_context
    # The caller (e.g., Postgres worker) should establish the app_context before calling this.
    # However, if app_for_context is passed, we should use it to ensure context is correct.
    # Using current_app directly might be problematic if called from a detached thread/process
    # without an active app context pushed by the caller.
    
    # The `with app_for_context.app_context():` block is generally handled by the caller of individual handlers.
    # The handlers themselves (like handle_process_single_email) then re-establish context
    # or assume it's present. For clarity, let's assume the app_for_context IS the app to use.

    if task_type == 'process_single_email':
        # handle_process_single_email itself manages its app context using current_app or passed app
        return handle_process_single_email(payload)
    elif task_type == 'poll_all_new_emails':
        # poll_new_emails takes app_instance and establishes context
        logging.info(f"[TaskDispatcher] Handling 'poll_all_new_emails' task.")
        poll_new_emails(app_for_context) 
        return {"status": "success", "message": "Email polling cycle initiated/completed."}
    elif task_type == 'new_whatsapp_message':
        logging.info(f"[TaskDispatcher] Handling 'new_whatsapp_message' task.")
        # Pass the app_for_context to the new handler
        return handle_new_whatsapp_message(payload, app_for_context)
    else:
        logging.error(f"[TaskDispatcher] Unknown task type: {task_type}")
        raise ValueError(f"Unknown task type: {task_type}") 