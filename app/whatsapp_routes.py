import hashlib
import hmac
import logging
import json
from flask import Blueprint, request, jsonify, current_app, Response
from .extensions import db
from .models import Inquiry, WhatsAppMessage, PendingTask
from datetime import datetime, timezone

whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')

logger = logging.getLogger(__name__)

def verify_waapi_signature(payload, signature_header, secret):
    """Verifies the HMAC-SHA256 signature from WaAPI."""
    if not signature_header:
        logger.warning("Webhook received without X-Waapi-HMAC header.")
        return False
    
    expected_signature = hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
    
    if not hmac.compare_digest(expected_signature, signature_header):
        logger.warning(f"Webhook signature mismatch. Expected: {expected_signature}, Got: {signature_header}")
        return False
    return True

def verify_signature(payload_body_bytes, secret_token, signature_header):
    """Verify the HMAC-SHA256 signature of the request.
    
    Args:
        payload_body_bytes: The raw request body as bytes.
        secret_token: The webhook secret token.
        signature_header: The signature received in the request header.
    Returns:
        bool: True if the signature is valid, False otherwise.
    """
    if not signature_header:
        current_app.logger.warning("Webhook signature header missing.")
        return False
    
    # Green API documentation typically states it sends the pure HMAC-SHA256 hex digest.
    # No need to split "sha256=" prefix if it's not there.
    received_signature = signature_header

    # Calculate the expected signature
    hashed_payload = hmac.new(secret_token.encode('utf-8'),
                              payload_body_bytes, 
                              hashlib.sha256).hexdigest()
    
    current_app.logger.debug(f"Calculated HMAC: {hashed_payload}")
    current_app.logger.debug(f"Received HMAC: {received_signature}")
    
    # Securely compare the signatures
    return hmac.compare_digest(hashed_payload, received_signature)

@whatsapp_bp.route('/webhook', methods=['POST'])
def waapi_webhook():
    logger.info("Received a request on /whatsapp/webhook")
    raw_payload = request.get_data() # Get raw data for signature verification
    signature = request.headers.get('X-Waapi-HMAC') # Or X-Waapi-Hmac
    
    webhook_secret = current_app.config.get('WAAPI_WEBHOOK_SECRET')
    if not webhook_secret:
        logger.error("WAAPI_WEBHOOK_SECRET is not configured.")
        return jsonify({"status": "error", "message": "Internal server error: Webhook secret not configured"}), 500

    if not verify_waapi_signature(raw_payload, signature, webhook_secret):
        logger.error("Webhook signature verification failed.")
        return jsonify({"status": "error", "message": "Invalid signature"}), 401

    try:
        data = request.get_json()
        if not data:
            logger.warning("Webhook received empty or non-JSON payload after signature verification.")
            return jsonify({"status": "error", "message": "Invalid payload"}), 400
        logger.info(f"Webhook payload validated and parsed: {data}")
    except Exception as e:
        logger.error(f"Error parsing JSON payload: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Could not parse JSON payload"}), 400

    event_type = data.get('event')
    
    if event_type == 'message': # Assuming 'message' is the event type for new messages
        message_data = data.get('message')
        if not message_data:
            logger.warning("Event type 'message' but no 'message' data found.")
            return jsonify({"status": "error", "message": "Missing message data"}), 400

        # --- Extract key fields (adjust based on actual WaAPI payload structure) ---
        wa_message_id = message_data.get('id') # Assuming WaAPI provides a unique message ID
        wa_chat_id = message_data.get('chatId') # Or 'from', 'sender.id' - needs confirmation from WaAPI docs for chat/user ID
        sender_number = message_data.get('from') # Or 'sender.phone'
        message_body = message_data.get('body')
        message_type = message_data.get('type', 'text') # Default to text, WaAPI might specify
        timestamp_ms = message_data.get('timestamp') # Assuming timestamp is provided (e.g., Unix ms)
        
        from_me = message_data.get('fromMe', False) # Indicates if the message was sent from the API account

        # Validate essential fields
        if not all([wa_message_id, wa_chat_id, sender_number]):
            logger.warning(f"Missing essential fields in message data: id={wa_message_id}, chatId={wa_chat_id}, sender={sender_number}")
            return jsonify({"status": "error", "message": "Missing essential message fields"}), 400
            
        # Convert timestamp (if provided and in ms)
        wa_timestamp = None
        if timestamp_ms:
            try:
                wa_timestamp = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc)
            except Exception as e:
                logger.warning(f"Could not parse WaAPI timestamp {timestamp_ms}: {e}")

        # --- Inquiry lookup/creation logic ---
        placeholder_email = f"whatsapp_{wa_chat_id}@internal.placeholder"
        inquiry = Inquiry.query.filter_by(primary_email_address=placeholder_email).first()

        new_inquiry_created = False
        if not inquiry:
            logger.info(f"No existing inquiry found for wa_chat_id (via placeholder: {placeholder_email}). Creating new inquiry.")
            inquiry = Inquiry(
                primary_email_address=placeholder_email,
                status="new_whatsapp" # A new status to indicate origin
            )
            db.session.add(inquiry)
            new_inquiry_created = True
        else:
            logger.info(f"Found existing inquiry ID {inquiry.id} for wa_chat_id (via placeholder: {placeholder_email}).")

        # --- Save WhatsAppMessage ---
        new_whatsapp_message = WhatsAppMessage(
            id=str(wa_message_id), 
            inquiry_id=inquiry.id, # This will be set after inquiry is flushed or if it exists
            wa_chat_id=str(wa_chat_id),
            sender_number=str(sender_number),
            from_me=bool(from_me),
            message_type=str(message_type),
            body=message_body,
            wa_timestamp=wa_timestamp,
        )
        
        if new_inquiry_created:
             db.session.flush() # Assign an ID to the new inquiry
             new_whatsapp_message.inquiry_id = inquiry.id

        db.session.add(new_whatsapp_message)
        
        try:
            db.session.commit()
            logger.info(f"Successfully processed and stored message ID {wa_message_id} for inquiry {inquiry.id}.")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Database error storing WhatsApp message: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Database error"}), 500

        return jsonify({"status": "success", "message": "Webhook processed"}), 200
    
    elif event_type:
        logger.info(f"Received webhook for unhandled event type: {event_type}")
        return jsonify({"status": "ignored", "message": f"Event type {event_type} not handled"}), 200
    else:
        logger.warning("Webhook received without an 'event' type.")
        return jsonify({"status": "error", "message": "Missing event type"}), 400 

@whatsapp_bp.route('/webhook', methods=['POST'])
def greenapi_webhook(): 
    """Handle incoming WhatsApp messages from Green API via Webhook."""
    current_app.logger.info(f"Incoming request to Green API webhook: {request.method} {request.url}")
    current_app.logger.info(f"Request Headers: {list(request.headers)}") # Log all headers

    secret = current_app.config.get('WAAPI_WEBHOOK_SECRET')
    if not secret:
        current_app.logger.error("CRITICAL: WAAPI_WEBHOOK_SECRET is not configured.")
        return jsonify({"status": "error", "message": "Webhook receiving endpoint not configured."}), 500

    raw_body_bytes = request.get_data()
    signature_header = request.headers.get('X-Waapi-Hmac') 

    if not signature_header:
        current_app.logger.warning("Webhook request missing 'X-Waapi-Hmac' header.")
        return jsonify({"status": "error", "message": "HMAC signature missing."}), 403

    if not verify_signature(raw_body_bytes, secret, signature_header):
        current_app.logger.warning("Webhook signature verification failed.")
        return jsonify({"status": "error", "message": "Invalid signature."}), 403

    current_app.logger.info("Webhook signature verified successfully.")

    try:
        message_data_str = raw_body_bytes.decode('utf-8')
        message_payload = json.loads(message_data_str) # This is the full Green API payload
        
        current_app.logger.debug(f"Decoded Webhook payload: {json.dumps(message_payload)}")

        # Create PendingTask to defer processing
        new_pending_task = PendingTask(
            task_type='process_whatsapp_message',
            payload=message_payload, # Store the entire Green API JSON payload
            status='pending',
            scheduled_for=datetime.now(timezone.utc) 
        )
        db.session.add(new_pending_task)
        db.session.commit()
        
        # Log the ID of the created PendingTask
        # The actual Green API message ID (e.g., idMessage) is inside message_payload
        green_api_message_id = message_payload.get('idMessage', 'N/A') 
        current_app.logger.info(f"Created PendingTask ID {new_pending_task.id} for 'process_whatsapp_message', Green API Message ID: {green_api_message_id}")

    except json.JSONDecodeError:
        current_app.logger.error("Webhook payload was not valid JSON.", exc_info=True)
        return jsonify({"status": "error", "message": "Invalid JSON payload."}), 400
    except Exception as e:
        current_app.logger.error(f"Error creating PendingTask for WhatsApp message: {e}", exc_info=True)
        db.session.rollback() # Rollback if PendingTask creation failed
        return jsonify({"status": "error", "message": "Internal server error creating processing task."}), 500

    return Response(status=200)

# Reminder: This blueprint needs to be registered in your Flask app factory.
# Example in app/__init__.py or main.py:
# def create_app(config_name='default'):
#     app = Flask(__name__)
#     app.config.from_object(config_by_name[config_name])
#     ...
#     from .whatsapp_routes import whatsapp_bp as wa_bp
#     app.register_blueprint(wa_bp)
#     ...
#     return app 