import os
import json
# import re # No longer needed here directly
# import threading # Moved to app.background_tasks
# import time # Moved to app.background_tasks
# import hashlib # No longer used
import traceback
from datetime import datetime, timedelta, timezone
import base64
# import webbrowser # No longer used
import logging
# from flask_sqlalchemy import SQLAlchemy # Handled in app package
# from sqlalchemy.exc import IntegrityError # Handled in app package

# Assume Flask, request, etc. are used by endpoints if kept here
# If endpoints are moved, these imports can be removed too.
from flask import Flask, request, jsonify, render_template, send_from_directory, session, redirect, url_for

# Import functions from service modules
from ms_graph_service import (
    fetch_emails as ms_fetch_emails,
    fetch_email_details as ms_fetch_email_details,
    fetch_attachments_list as ms_fetch_attachments_list,
    fetch_attachment_content as ms_fetch_attachment_content,
    fetch_new_emails_since as ms_fetch_new_emails_since
)
from data_extraction_service import (
    extract_travel_data,
    get_text_from_html
)

# Remove DB models import, they are used in background_tasks/app package now
# from models import Email, ExtractedData, AttachmentMetadata

# Remove the app initialization, it's handled in app package
# app = Flask(__name__, static_folder='static', template_folder='templates')
# app.secret_key = os.urandom(24)  # Required for sessions
# Remove DB config, handled in app package
# Remove logging config, assume handled in app package

# Remove global variable caches if not used by remaining endpoints
ms365_emails = [] # Keep this for now if UI relies on caching fetched emails list

# Remove Background Polling Setup & Functions - Moved to app/background_tasks.py
# POLL_INTERVAL_SECONDS = 120
# last_checked_timestamp = None
# stop_polling = threading.Event()
# def process_email_automatically(email_summary):
#    ...
# def poll_new_emails():
#    ...
# def background_poller():
#    ...

# --- API Endpoints (Keep for now, but consider moving to main app blueprints) ---

# Note: These endpoints will need access to the main `app` instance
# if they use decorators like @app.route. If moved to a Blueprint,
# they will use @blueprint_name.route instead.
# For now, we assume they might be registered later or are vestigial.

# @app.route('/') # Likely handled by main app routes
# def index():
#     return render_template('index.html', is_authenticated=True)

# @app.route('/api/emails', methods=['GET']) # Keep endpoint logic
def get_emails():
    """Fetch emails from Microsoft 365 via the service"""
    logging.info("API call: /api/emails")
    try:
        logging.info("Fetching emails using ms_graph_service...")
        max_emails = request.args.get('max', 20, type=int)

        # Use the service function
        emails = ms_fetch_emails(max_emails=max_emails)
        logging.info(f"Fetched {len(emails)} emails via service")

        # Cache the raw emails list if needed by UI interactions later
        global ms365_emails
        ms365_emails = emails # Overwrite cache with latest fetch
        
        # Format emails for the frontend
        formatted_emails = []
        for email in emails:
            formatted_email = {
                'id': email.get('id', ''),
                'subject': email.get('subject', '(No Subject)'),
            # Use 'sender' field which is generally more reliable for the actual sender
            'from': email.get('sender', {}).get('emailAddress', {}).get('address', '(Unknown)'),
            'sender_name': email.get('sender', {}).get('emailAddress', {}).get('name', ''),
                'preview': email.get('bodyPreview', '')[:100] + '...' if email.get('bodyPreview') else '',
                'date': email.get('receivedDateTime', ''),
            'hasAttachments': email.get('hasAttachments', False),
            'isRead': email.get('isRead', None) # Pass read status
            }
            formatted_emails.append(formatted_email)
        
        logging.info(f"Successfully formatted {len(emails)} emails for API response.")
        return jsonify({
            'success': True,
            'emails': formatted_emails
        })
    
    except Exception as e:
        logging.error(f"ERROR in get_emails API: {str(e)}", exc_info=True)
        # Check for specific auth errors if needed
        return jsonify({
            'success': False,
            'error': f"An error occurred: {str(e)}"
        }), 500

# @app.route('/api/email/<email_id>', methods=['GET']) # Keep endpoint logic
def get_email_content(email_id):
    """Get content of a specific email via the service"""
    logging.info(f"API call: /api/email/{email_id}")
    try:
        # Use the service function
        email_data = ms_fetch_email_details(email_id)

        if email_data:
            # Extract text from HTML using the service function
            body_content = email_data.get("body", {})
            html_content = body_content.get("content", "")
            text_content = get_text_from_html(html_content) # Use service function
            
            # Format email data for frontend
            formatted_email = {
                'id': email_data.get('id', ''),
                'subject': email_data.get('subject', '(No Subject)'),
                'from': email_data.get('from', {}).get('emailAddress', {}).get('address', '(Unknown)'),
                'sender_name': email_data.get('from', {}).get('emailAddress', {}).get('name', ''),
                'to': [r.get('emailAddress', {}).get('address', '') for r in email_data.get('toRecipients', [])],
                'date': email_data.get('receivedDateTime', ''),
                'html_content': html_content,
                'text_content': text_content,
                'hasAttachments': email_data.get('hasAttachments', False)
            }
            
            logging.info(f"Successfully fetched and formatted content for email ID: {email_id}")
            return jsonify({
                'success': True,
                'email': formatted_email
            })
        else:
            logging.warning(f"Email not found or error fetching details for ID: {email_id}")
            return jsonify({
                'success': False,
                'error': f"Could not retrieve email details for ID: {email_id}"
            }), 404
    
    except Exception as e:
        logging.error(f"Error in get_email_content API for {email_id}: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# @app.route('/api/attachments/<email_id>', methods=['GET']) # Keep endpoint logic
def get_attachments(email_id):
    """Get attachments list for a specific email via the service"""
    logging.info(f"API call: /api/attachments/{email_id}")
    try:
        # Use the service function
        attachments = ms_fetch_attachments_list(email_id)

        # Format attachment data (already done well in service, just pass through)
        formatted_attachments = []
        for attachment in attachments:
            formatted_attachment = {
                'id': attachment.get('id', ''),
                'name': attachment.get('name', ''),
                'contentType': attachment.get('contentType', ''),
                'size': attachment.get('size', 0)
            }
            formatted_attachments.append(formatted_attachment)
        

        logging.info(f"Successfully fetched {len(formatted_attachments)} attachment(s) list for email ID: {email_id}")
        return jsonify({
            'success': True,
            'attachments': formatted_attachments
        })
    
    except Exception as e:
        logging.error(f"Error in get_attachments API for {email_id}: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# @app.route('/api/attachment/<email_id>/<attachment_id>', methods=['GET']) # Keep endpoint logic
def download_attachment(email_id, attachment_id):
    """Download a specific attachment via the service"""
    logging.info(f"API call: /api/attachment/{email_id}/{attachment_id}")
    try:
        # Use the service function
        attachment_data = ms_fetch_attachment_content(email_id, attachment_id)

        if attachment_data and 'content' in attachment_data:
            # Return the attachment data (already decoded in service)
            # Encode back to base64 for JSON transport if needed by frontend
            content_bytes_b64 = base64.b64encode(attachment_data['content']).decode('utf-8')

            logging.info(f"Successfully fetched attachment content for {attachment_id} from email {email_id}")
            return jsonify({
                'success': True,
                'name': attachment_data['name'],
                'contentType': attachment_data['contentType'],
                'size': attachment_data['size'],
                # Send content as base64 string
                'data': content_bytes_b64
            })
        else:
            logging.warning(f"Attachment content not found or error fetching {attachment_id} from {email_id}")
            return jsonify({
                'success': False,
                'error': 'Attachment content could not be retrieved'
            }), 404
    
    except Exception as e:
        logging.error(f"Error in download_attachment API for {attachment_id}: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# @app.route('/api/extract', methods=['POST']) # Keep endpoint logic
def extract_data_api():
    """
    Extract travel data from provided content using the extraction service.
    This endpoint is for MANUAL extraction triggered by the UI, not the automatic polling.
    """
    logging.info("API call: /api/extract")
    try:
        data = request.json
        # Expecting raw HTML content from the email body display
        email_content_html = data.get('content', '')
        
        if not email_content_html:
            logging.warning("Extraction request received with no HTML content.")
            return jsonify({
                'success': False,
                'error': 'No content provided'
            }), 400
        
        # --- Extraction Logic ---
        logging.info("Starting manual data extraction process...")
        # Use the unified extraction service function
        extracted_data, extraction_source = extract_travel_data(email_content_html)
        logging.info(f"Manual extraction performed. Source: {extraction_source}")

        # --- Data Validation (same logic as automatic processing) ---
        logging.info("Performing data validation...")
        essential_fields = [
            "first_name", "last_name", 
            "travel_start_date", "travel_end_date", 
            "trip_cost"
        ]
        missing_fields = [field for field in essential_fields if not extracted_data.get(field)]
        validation_status = "Incomplete" if missing_fields else "Complete"
                
        if missing_fields:
            logging.warning(f"Manual Validation Incomplete. Missing fields: {missing_fields}")
        else:
            logging.info("Manual Validation Complete. All essential fields present.")

        # --- TODO: Optional DB Integration for Manual Extraction ---
        # Should manual extraction results update the database?
        # If yes, you'd need the email_id associated with this content.
        # The frontend would need to pass the email_id along with the content.
        # db_update_extracted_data(email_id=data.get('email_id'), extracted_data=extracted_data, ...)
        logging.info("TODO: Consider if manual extraction results should update the DB.")
        # --- ---
            
        # --- Return Response ---
        return jsonify({
            'success': True,
            'extracted_data': extracted_data,
            'source': extraction_source,
            'validation_status': validation_status,
            'missing_fields': missing_fields
        })
    
    except Exception as e:
        logging.error(f"Exception in /api/extract: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f"An error occurred during extraction: {str(e)}"
        }), 500

# Remove Database Initialization - Handled in app package
# def init_db():
#    ...

# Remove main execution block - Handled by main.py and app package
# if __name__ == '__main__':
#    ... 