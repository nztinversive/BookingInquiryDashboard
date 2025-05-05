import logging
# import threading # Removed
# import time # Removed
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
from data_extraction_service import extract_travel_data, classify_email_intent

# --- Removed Background Polling Setup ---
# POLL_INTERVAL_SECONDS = 120
# last_checked_timestamp = None
# stop_polling = threading.Event()
# polling_thread = None

# --- Kept process_email_automatically for reference, but it's now superseded by tasks.process_single_email ---
# Note: This function is no longer called by the polling mechanism.
# It could potentially be repurposed or removed entirely later.
# It still depends on app, db, models being passed in.

# def process_email_automatically(app, db, Email, ExtractedData, AttachmentMetadata, email_summary, classified_intent):
#     """
#     [DEPRECATED - Logic moved to tasks.process_single_email]
#     Fetches full email, finds/creates Inquiry, links email, extracts data,
#     merges data into Inquiry's ExtractedData, and saves to DB.
#     Also saves the pre-classified intent.
#     """
#     # ... [Function body remains here, but commented out or unused] ...
#     pass


# --- Removed poll_new_emails --- 
# Logic moved to tasks.poll_and_dispatch_emails
# def poll_new_emails(app, db, Email, ExtractedData, AttachmentMetadata, Inquiry):
#    pass

# --- Removed background_poller --- 
# def background_poller(app):
#    pass

# --- Removed start_background_polling --- 
# def start_background_polling(app):
#    pass

# --- Removed shutdown_background_polling --- 
# def shutdown_background_polling():
#    pass

# --- Kept NEGATIVE_FILTERS definition if it's not defined elsewhere, though it's also copied to tasks.py ---
# Ensure this is defined in one canonical place (e.g., config or tasks.py)
NEGATIVE_FILTERS = {
    "senders": ["no-reply@", "noreply@", "support@", "mailer-daemon@", "postmaster@", "bounce@", "info@", "newsletter@", "updates@"],
    "subjects": ["undeliverable:", "delivery status notification", "out of office", "automatic reply", "newsletter", "update", "promotion"]
} 