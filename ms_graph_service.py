import os
import time
import logging
import traceback
import base64

import msal
import requests

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Store the access token (simple in-memory cache)
_ms365_token_cache = {
    "token": None,
    "expires_at": 0
}

def get_ms365_config():
    """Loads MS365 configuration from environment variables."""
    config = {
        "client_id": os.environ.get("MS365_CLIENT_ID"),
        "client_secret": os.environ.get("MS365_CLIENT_SECRET"),
        "tenant_id": os.environ.get("MS365_TENANT_ID"),
        "target_email": os.environ.get("MS365_TARGET_EMAIL")
    }
    missing = [key for key, value in config.items() if not value]
    if missing:
        raise ValueError(f"Missing MS365 configuration in Replit Secrets: {', '.join(missing)}")
    return config

def get_access_token():
    """Gets a Graph API access token using client credentials, caching it."""
    global _ms365_token_cache

    # Return cached token if available and not expired (with a small buffer)
    if _ms365_token_cache["token"] and _ms365_token_cache["expires_at"] > time.time() + 60:
        logging.debug("Using cached MS365 token.")
        return _ms365_token_cache["token"]

    logging.info("Attempting to get new MS365 token...")
    try:
        config = get_ms365_config()
        authority = f"https://login.microsoftonline.com/{config['tenant_id']}"
        app = msal.ConfidentialClientApplication(
            config['client_id'],
            authority=authority,
            client_credential=config['client_secret']
        )

        logging.info("Requesting token with client credentials...")
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

        if "access_token" in result:
            logging.info("New MS365 token acquired successfully.")
            _ms365_token_cache["token"] = result['access_token']
            _ms365_token_cache["expires_at"] = time.time() + result.get('expires_in', 3599)
            return _ms365_token_cache["token"]
        else:
            error = result.get("error", "Unknown error")
            error_desc = result.get("error_description", "No error description")
            error_msg = f"MS365 Authentication failed: {error}. {error_desc}"
            logging.error(error_msg)
            raise Exception(error_msg)
    except ValueError as ve:
        logging.error(f"Configuration Error: {ve}")
        raise
    except Exception as e:
        logging.error(f"Error getting MS365 token: {e}")
        logging.debug(traceback.format_exc()) # Log stack trace for debugging
        raise

def _make_graph_api_call(method, endpoint, params=None, json_data=None):
    """Helper function to make authenticated calls to the Graph API."""
    try:
        token = get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        logging.debug(f"Making Graph API call: {method} {endpoint} with params: {params}")
        response = requests.request(method, endpoint, headers=headers, params=params, json=json_data)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        logging.debug(f"Graph API call successful: {response.status_code}")
        # Handle potential empty responses for certain status codes like 204
        if response.status_code == 204:
             return None
        return response.json()
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Graph API request failed: {req_err}")
        # Log response body if available for more context
        if req_err.response is not None:
            logging.error(f"Response Status: {req_err.response.status_code}")
            logging.error(f"Response Body: {req_err.response.text}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error during Graph API call: {e}")
        logging.debug(traceback.format_exc())
        raise

def fetch_emails(max_emails=20):
    """Fetches recent emails for the configured target user."""
    logging.info(f"Fetching up to {max_emails} emails...")
    try:
        config = get_ms365_config()
        endpoint = f"https://graph.microsoft.com/v1.0/users/{config['target_email']}/messages"
        params = {
            '$top': max_emails,
            '$select': 'id,subject,sender,from,toRecipients,receivedDateTime,bodyPreview,hasAttachments,isRead',
            '$orderby': 'receivedDateTime desc'
        }
        data = _make_graph_api_call("GET", endpoint, params=params)
        emails = data.get("value", []) if data else []
        logging.info(f"Successfully fetched {len(emails)} emails.")
        return emails
    except Exception as e:
        logging.error(f"Failed to fetch emails: {e}")
        return [] # Return empty list on error

def fetch_email_details(email_id):
    """Fetches full details for a specific email, including the body."""
    logging.info(f"Fetching details for email ID: {email_id}")
    try:
        config = get_ms365_config()
        endpoint = f"https://graph.microsoft.com/v1.0/users/{config['target_email']}/messages/{email_id}"
        params = {
            # Request body in HTML format
            '$select': 'id,subject,from,toRecipients,receivedDateTime,body,hasAttachments'
        }
        email_data = _make_graph_api_call("GET", endpoint, params=params)
        logging.info(f"Successfully fetched details for email ID: {email_id}")
        return email_data
    except Exception as e:
        logging.error(f"Failed to fetch email details for {email_id}: {e}")
        return None # Return None on error

def fetch_new_emails_since(timestamp):
    """Fetches emails received after a specific timestamp."""
    logging.info(f"Polling for new emails since: {timestamp.isoformat()}")
    try:
        config = get_ms365_config()
        endpoint = f"https://graph.microsoft.com/v1.0/users/{config['target_email']}/messages"
        # Format timestamp for Graph API filter (ISO 8601 UTC)
        filter_time_str = timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
        filter_query = f"receivedDateTime gt {filter_time_str}"

        params = {
            '$top': 50, # Limit results per poll
            '$select': 'id,subject,receivedDateTime,isRead', # Select only needed fields for polling
            '$filter': filter_query,
            '$orderby': 'receivedDateTime asc' # Process oldest first within the new batch
        }
        data = _make_graph_api_call("GET", endpoint, params=params)
        new_emails = data.get("value", []) if data else []
        if new_emails:
            logging.info(f"Found {len(new_emails)} new email(s) since last check.")
        else:
            logging.debug("No new emails found since last check.")
        return new_emails
    except Exception as e:
        logging.error(f"Error polling new emails: {e}")
        return [] # Return empty list on error

def fetch_attachments_list(email_id):
    """Fetches the list of attachments for a specific email."""
    logging.info(f"Fetching attachment list for email ID: {email_id}")
    try:
        config = get_ms365_config()
        endpoint = f"https://graph.microsoft.com/v1.0/users/{config['target_email']}/messages/{email_id}/attachments"
        params = {
            '$select': 'id,name,contentType,size' # Select only metadata
        }
        data = _make_graph_api_call("GET", endpoint, params=params)
        attachments = data.get("value", []) if data else []
        logging.info(f"Found {len(attachments)} attachments for email {email_id}.")
        return attachments
    except Exception as e:
        logging.error(f"Failed to fetch attachments list for {email_id}: {e}")
        return []

def fetch_attachment_content(email_id, attachment_id):
    """Fetches the content of a specific attachment."""
    logging.info(f"Fetching content for attachment ID: {attachment_id} from email: {email_id}")
    try:
        config = get_ms365_config()
        # Need to fetch the attachment resource which includes contentBytes
        endpoint = f"https://graph.microsoft.com/v1.0/users/{config['target_email']}/messages/{email_id}/attachments/{attachment_id}"
        # No need for $select if we want the default response which includes contentBytes
        attachment_data = _make_graph_api_call("GET", endpoint)

        if attachment_data and 'contentBytes' in attachment_data:
            logging.info(f"Successfully fetched content for attachment: {attachment_data.get('name')}")
            # Decode base64 content
            decoded_content = base64.b64decode(attachment_data['contentBytes'])
            return {
                'name': attachment_data.get('name', 'attachment'),
                'contentType': attachment_data.get('contentType', 'application/octet-stream'),
                'size': attachment_data.get('size'),
                'content': decoded_content
            }
        else:
            logging.warning(f"Content bytes not found for attachment {attachment_id} in email {email_id}.")
            return None

    except Exception as e:
        logging.error(f"Failed to fetch attachment content for {attachment_id}: {e}")
        return None 