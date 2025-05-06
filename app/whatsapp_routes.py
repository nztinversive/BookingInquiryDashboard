import hashlib
import hmac
import logging
from flask import Blueprint, request, jsonify, current_app
from .extensions import db
from .models import Inquiry, WhatsAppMessage
from datetime import datetime, timezone

whatsapp_bp = Blueprint('whatsapp_bp', __name__, url_prefix='/whatsapp')

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