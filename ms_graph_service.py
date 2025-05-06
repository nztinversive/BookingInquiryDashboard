import os
import time
import logging
import traceback
import base64

import msal
import requests

# Tenacity imports for retrying
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from requests.exceptions import RequestException, HTTPError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Retry Configuration ---
# Define which exceptions should trigger a retry
def is_transient_error(exception):
    """Return True if the exception is a common transient network/server error."""
    if isinstance(exception, HTTPError):
        # Retry on common server errors and rate limiting
        # 429: Too Many Requests (Rate Limiting)
        # 500: Internal Server Error (might be temporary)
        # 502: Bad Gateway
        # 503: Service Unavailable
        # 504: Gateway Timeout
        return exception.response.status_code in [429, 500, 502, 503, 504]
    # Retry on general connection errors, timeouts, etc.
    return isinstance(exception, RequestException)

# Decorator for retrying Graph API calls
# Waits 2^x * 1 second between each retry, starting with 1 second, up to 30 seconds, for 5 attempts.
retry_graph_call = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception_type((RequestException, HTTPError)) # Simplified check, could use is_transient_error for more specific codes
)

# --- Module Level Configuration Store ---
# This will be populated by configure_ms_graph_client
_graph_config = {}

# Store the access token (simple in-memory cache)
_ms365_token_cache = {
    "token": None,
    "expires_at": 0
}

# --- Configuration Function (called from app factory) ---
def configure_ms_graph_client(config):
    """Loads MS Graph configuration from the Flask app config object."""
    global _graph_config
    _graph_config = {
        "client_id": config.get("MS_GRAPH_CLIENT_ID"),
        "client_secret": config.get("MS_GRAPH_CLIENT_SECRET"),
        "tenant_id": config.get("MS_GRAPH_TENANT_ID"),
        "mailbox_user_id": config.get("MS_GRAPH_MAILBOX_USER_ID") # Mailbox to monitor
    }
    missing = [key for key, value in _graph_config.items() if not value]
    if missing:
        logging.error(f"MS Graph configuration incomplete. Missing keys: {', '.join(missing)}")
        # Reset config if incomplete to prevent partial use
        _graph_config = {}
        return False
    else:
        logging.info("MS Graph client configuration loaded successfully.")
        return True

# --- Internal Helper Functions ---
def _ensure_config_loaded():
    """Checks if the configuration has been loaded."""
    if not _graph_config:
        raise RuntimeError("MS Graph client configuration has not been loaded. Call configure_ms_graph_client first.")
    return True

def get_access_token():
    """Gets a Graph API access token using client credentials, caching it."""
    global _ms365_token_cache

    # Check if config is loaded first
    _ensure_config_loaded()

    # Return cached token if available and not expired (with a small buffer)
    if _ms365_token_cache["token"] and _ms365_token_cache["expires_at"] > time.time() + 60:
        logging.debug("Using cached MS Graph token.")
        return _ms365_token_cache["token"]

    logging.info("Attempting to get new MS Graph token...")
    try:
        # Access config from module-level variable
        authority = f"https://login.microsoftonline.com/{_graph_config['tenant_id']}"
        app = msal.ConfidentialClientApplication(
            _graph_config['client_id'],
            authority=authority,
            client_credential=_graph_config['client_secret']
        )

        logging.info("Requesting token with client credentials...")
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

        if "access_token" in result:
            logging.info("New MS Graph token acquired successfully.")
            _ms365_token_cache["token"] = result['access_token']
            _ms365_token_cache["expires_at"] = time.time() + result.get('expires_in', 3599)
            return _ms365_token_cache["token"]
        else:
            error = result.get("error", "Unknown error")
            error_desc = result.get("error_description", "No error description")
            error_msg = f"MS Graph Authentication failed: {error}. {error_desc}"
            logging.error(error_msg)
            raise Exception(error_msg)
    except KeyError as ke:
         logging.error(f"Missing configuration key during token acquisition: {ke}")
         raise RuntimeError(f"Configuration error: Missing key {ke}. Ensure configure_ms_graph_client was called successfully.") from ke
    except Exception as e:
        logging.error(f"Error getting MS Graph token: {e}")
        logging.debug(traceback.format_exc()) # Log stack trace for debugging
        raise

# Apply retry logic ONLY to the function making the actual network call
@retry_graph_call
def _make_graph_api_call(method, endpoint, params=None, json_data=None):
    """Helper function to make authenticated calls to the Graph API with retry logic."""
    # Check if config is loaded (implicitly checked by get_access_token)
    try:
        token = get_access_token() # This now ensures config is loaded
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        logging.debug(f"Making Graph API call: {method} {endpoint} with params: {params}")
        response = requests.request(method, endpoint, headers=headers, params=params, json=json_data)
        
        # Log attempt details (useful for retry debugging)
        attempt_number = _make_graph_api_call.retry.statistics.get('attempt_number', 1)
        if attempt_number > 1:
            logging.warning(f"Graph API call attempt {attempt_number} for {method} {endpoint}")
            
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        logging.debug(f"Graph API call successful: {response.status_code}")
        # Handle potential empty responses for certain status codes like 204
        if response.status_code == 204:
             return None
        return response.json()
    except (RequestException, HTTPError) as req_err:
        # Log specific error before raising it for tenacity to catch
        logging.warning(f"Graph API call failed (attempt {_make_graph_api_call.retry.statistics.get('attempt_number', 1)}): {req_err}")
        if hasattr(req_err, 'response') and req_err.response is not None:
            logging.warning(f"Response Status: {req_err.response.status_code}, Body: {req_err.response.text[:500]}...") # Log truncated body
        raise # Re-raise the exception for tenacity to handle retry/stop
    except Exception as e:
        # Catch other unexpected errors (e.g., JSON decoding, issues in get_access_token)
        logging.error(f"Unexpected error during Graph API call attempt {_make_graph_api_call.retry.statistics.get('attempt_number', 1)}: {e}")
        logging.debug(traceback.format_exc())
        raise # Re-raise to signal failure

# --- Public API Functions ---

def fetch_emails(max_emails=20):
    """Fetches recent emails for the configured target user."""
    logging.info(f"Fetching up to {max_emails} emails...")
    try:
        _ensure_config_loaded() # Ensure config is loaded
        target_user = _graph_config.get('mailbox_user_id')
        if not target_user:
             raise RuntimeError("Mailbox user ID not configured.")

        endpoint = f"https://graph.microsoft.com/v1.0/users/{target_user}/messages"
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
        _ensure_config_loaded()
        target_user = _graph_config.get('mailbox_user_id')
        if not target_user:
             raise RuntimeError("Mailbox user ID not configured.")

        endpoint = f"https://graph.microsoft.com/v1.0/users/{target_user}/messages/{email_id}"
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
        _ensure_config_loaded()
        target_user = _graph_config.get('mailbox_user_id')
        if not target_user:
             raise RuntimeError("Mailbox user ID not configured.")

        endpoint = f"https://graph.microsoft.com/v1.0/users/{target_user}/messages"
        # Format timestamp for Graph API filter (ISO 8601 UTC)
        filter_time_str = timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
        filter_query = f"receivedDateTime gt {filter_time_str}"

        params = {
            '$top': 50, # Limit results per poll
            '$select': 'id,subject,receivedDateTime,isRead,from,bodyPreview', # Added isRead, from, bodyPreview
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
        _ensure_config_loaded()
        target_user = _graph_config.get('mailbox_user_id')
        if not target_user:
             raise RuntimeError("Mailbox user ID not configured.")

        endpoint = f"https://graph.microsoft.com/v1.0/users/{target_user}/messages/{email_id}/attachments"
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
        _ensure_config_loaded()
        target_user = _graph_config.get('mailbox_user_id')
        if not target_user:
             raise RuntimeError("Mailbox user ID not configured.")

        # Need to fetch the attachment resource which includes contentBytes
        endpoint = f"https://graph.microsoft.com/v1.0/users/{target_user}/messages/{email_id}/attachments/{attachment_id}"
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

# --- Functions for Modifying Email State (Example: Mark as Read) ---
# Apply retry logic here as well
@retry_graph_call
def mark_email_as_read(email_id):
    # ... (implementation using _make_graph_api_call or direct requests with PATCH) ...
    pass

@retry_graph_call
def move_email(email_id, destination_folder_id):
    # ... (implementation using _make_graph_api_call or direct requests with POST) ...
    pass 