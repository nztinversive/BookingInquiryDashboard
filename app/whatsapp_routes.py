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

# The verify_waapi_signature and verify_signature functions are no longer needed
# as Green API appears to be sending a direct Bearer token instead of an HMAC signature.

# def verify_waapi_signature(payload, signature_header, secret):
#     """Verifies the HMAC-SHA256 signature from WaAPI."""
#     if not signature_header:
#         # logger.warning("Webhook received without X-Waapi-HMAC header.")
#         current_app.logger.warning("Webhook received without X-Waapi-HMAC header.")
#         return False
#     
#     expected_signature = hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
#     
#     if not hmac.compare_digest(expected_signature, signature_header):
#         # logger.warning(f"Webhook signature mismatch. Expected: {expected_signature}, Got: {signature_header}")
#         current_app.logger.warning(f"Webhook signature mismatch. Expected: {expected_signature}, Got: {signature_header}")
#         return False
#     return True

# def verify_signature(payload_body_bytes, secret_token, signature_header):
#     """Verify the HMAC-SHA256 signature of the request.
#     Args:
#         payload_body_bytes: The raw request body as bytes.
#         secret_token: The webhook secret token.
#         signature_header: The signature received in the request header.
#     Returns:
#         bool: True if the signature is valid, False otherwise.
#     """
#     if not signature_header:
#         current_app.logger.warning("Webhook signature header missing.")
#         return False
#     
#     received_signature = signature_header
#     hashed_payload = hmac.new(secret_token.encode('utf-8'),
#                               payload_body_bytes, 
#                               hashlib.sha256).hexdigest()
#     current_app.logger.debug(f"Calculated HMAC: {hashed_payload}")
#     current_app.logger.debug(f"Received HMAC: {received_signature}")
#     return hmac.compare_digest(hashed_payload, received_signature)

# Commented out waapi_webhook as greenapi_webhook is the active one
# @whatsapp_bp.route('/webhook', methods=['POST'])
# def waapi_webhook():
#     ...

@whatsapp_bp.route('/webhook', methods=['POST'])
def greenapi_webhook(): 
    """Handle incoming WhatsApp messages from Green API via Webhook by checking Bearer token."""
    current_app.logger.info(f"Incoming request to Green API webhook: {request.method} {request.url}")
    current_app.logger.info(f"Request Headers: {list(request.headers)}")

    expected_secret = current_app.config.get('WAAPI_WEBHOOK_SECRET')
    if not expected_secret:
        current_app.logger.error("CRITICAL: WAAPI_WEBHOOK_SECRET is not configured.")
        return jsonify({"status": "error", "message": "Webhook receiving endpoint not configured."}), 500

    auth_header = request.headers.get('Authorization')
    token_verified = False
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == 'bearer':
            received_token = parts[1]
            # Use hmac.compare_digest for secure comparison against timing attacks, though less critical here than with HMACs
            if hmac.compare_digest(received_token, expected_secret):
                token_verified = True
            else:
                current_app.logger.warning(f"Authorization token mismatch. Received: {received_token}")
        else:
            current_app.logger.warning(f"Malformed Authorization header: {auth_header}")
    else:
        current_app.logger.warning("Webhook request missing 'Authorization' header.")

    if not token_verified:
        current_app.logger.warning("Webhook authorization failed.")
        return jsonify({"status": "error", "message": "Invalid authorization."}), 403 # 403 Forbidden or 401 Unauthorized

    current_app.logger.info("Webhook authorization successful.")

    raw_body_bytes = request.get_data()
    try:
        message_data_str = raw_body_bytes.decode('utf-8')
        message_payload = json.loads(message_data_str)
        current_app.logger.debug(f"Decoded Webhook payload: {json.dumps(message_payload)}")

        new_pending_task = PendingTask(
            task_type='new_whatsapp_message',
            payload=message_payload, 
            status='pending',
            scheduled_for=datetime.now(timezone.utc) 
        )
        db.session.add(new_pending_task)
        db.session.commit()
        
        green_api_message_id = message_payload.get('idMessage', 'N/A') 
        current_app.logger.info(f"Created PendingTask ID {new_pending_task.id} for 'new_whatsapp_message', Green API Message ID: {green_api_message_id}")

    except json.JSONDecodeError:
        current_app.logger.error("Webhook payload was not valid JSON.", exc_info=True)
        return jsonify({"status": "error", "message": "Invalid JSON payload."}), 400
    except Exception as e:
        current_app.logger.error(f"Error creating PendingTask for WhatsApp message: {e}", exc_info=True)
        db.session.rollback()
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