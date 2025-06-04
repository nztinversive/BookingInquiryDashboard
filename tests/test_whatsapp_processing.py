import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from flask import Flask

# Adjust these imports based on your project structure
from app.background_tasks import handle_new_whatsapp_message
from app.models import Inquiry, WhatsAppMessage, ExtractedData, db
# from app import create_app # If you have a full app factory for testing

# Minimal Flask app for context in tests
@pytest.fixture(scope='module')
def test_app():
    app = Flask(__name__)
    app.config['TESTING'] = True
    # In-memory SQLite for testing, or configure as needed
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Mock essential_fields from config
    app.config['ESSENTIAL_EXTRACTION_FIELDS'] = ["first_name", "last_name", "email"]

    db.init_app(app)

    with app.app_context():
        db.create_all() # Create tables for the in-memory database
        yield app # Provide the app object to tests
        db.drop_all() # Clean up after tests in this module

@pytest.fixture
def app_context(test_app):
    """Fixture to provide an app context and clean DB for each test function."""
    with test_app.app_context():
        db.session.remove() # Ensure session is clean
        db.drop_all()       # Drop all tables for isolation
        db.create_all()     # Recreate tables
        yield

# Sample Green API Payloads (adapt structure from actual Green API documentation)
SAMPLE_TEXT_MESSAGE_PAYLOAD = {
    "typeWebhook": "incomingMessageReceived",
    "instanceData": {"idInstance": 123, "wid": "[email protected]", "typeInstance": "whatsapp"},
    "timestamp": int(datetime.now(timezone.utc).timestamp()),
    "idMessage": "testmsg001",
    "senderData": {"chatId": "[email protected]", "sender": "[email protected]", "senderName": "Test User"},
    "messageData": {
        "typeMessage": "textMessage",
        "textMessageData": {"textMessage": "Hello, I need a quote. My name is John Doe. Email: [email protected]"}
    }
}

SAMPLE_MEDIA_MESSAGE_PAYLOAD = {
    "typeWebhook": "incomingMessageReceived",
    "instanceData": {"idInstance": 123, "wid": "[email protected]", "typeInstance": "whatsapp"},
    "timestamp": int(datetime.now(timezone.utc).timestamp()), 
    "idMessage": "testmsg002",
    "senderData": {"chatId": "[email protected]", "sender": "[email protected]", "senderName": "Jane Media"},
    "messageData": {
        "typeMessage": "imageMessage",
        "imageMessageData": {
            "downloadUrl": "https://example.com/image.jpg",
            "mimeType": "image/jpeg",
            "caption": "My flight ticket. Need insurance. Jane TicketHolder, [email protected]",
            "fileName": "ticket.jpg"
        }
    }
}

@patch('app.background_tasks.extract_travel_data') 
def test_process_new_text_message_creates_inquiry(mock_extract_travel_data, app_context, test_app):
    mock_extract_travel_data.return_value = ({"first_name": "John", "last_name": "Doe", "email": "[email protected]"}, "openai_extracted")
    payload = SAMPLE_TEXT_MESSAGE_PAYLOAD.copy()
    payload['idMessage'] = 'test_new_inquiry_01' 
    result = handle_new_whatsapp_message(payload, test_app)
    assert result['status'] == 'success'
    inquiry = db.session.get(Inquiry, result['inquiry_id'])
    assert inquiry is not None
    expected_inquiry_identifier = f"whatsapp_{payload['senderData']['chatId']}@internal.placeholder"
    assert inquiry.primary_email_address == expected_inquiry_identifier
    assert inquiry.status == 'Complete'
    wa_message = db.session.get(WhatsAppMessage, 'test_new_inquiry_01')
    assert wa_message is not None
    assert wa_message.inquiry_id == inquiry.id
    extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=inquiry.id).first()
    assert extracted_data is not None
    assert extracted_data.data['first_name'] == "John"
    mock_extract_travel_data.assert_called_once_with(payload['messageData']['textMessageData']['textMessage'])

@patch('app.background_tasks.extract_travel_data')
def test_process_message_links_to_existing_inquiry(mock_extract_travel_data, app_context, test_app):
    payload = SAMPLE_TEXT_MESSAGE_PAYLOAD.copy()
    payload['idMessage'] = 'test_existing_inquiry_01'
    
    # Create existing Inquiry
    existing_inquiry_identifier = f"whatsapp_{payload['senderData']['chatId']}@internal.placeholder"
    existing_inquiry = Inquiry(primary_email_address=existing_inquiry_identifier, status='Incomplete')
    db.session.add(existing_inquiry)
    db.session.commit()
    db.session.refresh(existing_inquiry) # Ensure we have the ID

    mock_extract_travel_data.return_value = ({"first_name": "John", "email": "[email protected]"}, "openai_followup")

    result = handle_new_whatsapp_message(payload, test_app)

    assert result['status'] == 'success'
    assert result['inquiry_id'] == existing_inquiry.id

    wa_message = db.session.get(WhatsAppMessage, 'test_existing_inquiry_01')
    assert wa_message is not None
    assert wa_message.inquiry_id == existing_inquiry.id

    updated_inquiry = db.session.get(Inquiry, existing_inquiry.id)
    # Status might still be incomplete if last_name is essential, or complete if not.
    # Based on essential_fields ["first_name", "last_name", "email"], it should be incomplete.
    assert updated_inquiry.status == 'Incomplete' # Because 'last_name' is missing from mocked extraction
    
    extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=existing_inquiry.id).first()
    assert extracted_data is not None
    assert extracted_data.data['first_name'] == "John"
    assert extracted_data.data.get('last_name') is None # Check that it's not there
    assert extracted_data.missing_fields == 'last_name'
    mock_extract_travel_data.assert_called_once()


@patch('app.background_tasks.extract_travel_data')
def test_process_duplicate_message_id_skips(mock_extract_travel_data, app_context, test_app):
    payload = SAMPLE_TEXT_MESSAGE_PAYLOAD.copy()
    payload['idMessage'] = 'duplicate_msg_001'

    # Pre-populate the WhatsAppMessage
    # First, ensure an inquiry exists to link to
    inquiry_identifier = f"whatsapp_{payload['senderData']['chatId']}@internal.placeholder"
    inquiry = Inquiry(primary_email_address=inquiry_identifier, status='new_whatsapp')
    db.session.add(inquiry)
    db.session.commit()
    db.session.refresh(inquiry)

    existing_message = WhatsAppMessage(
        id='duplicate_msg_001', 
        inquiry_id=inquiry.id, 
        wa_chat_id=payload['senderData']['chatId'],
        sender_number=payload['senderData']['sender'],
        message_type='textMessage',
        body='Original message content'
    )
    db.session.add(existing_message)
    db.session.commit()

    result = handle_new_whatsapp_message(payload, test_app)

    assert result['status'] == 'skipped'
    assert 'Duplicate WhatsApp message ID' in result['message']
    mock_extract_travel_data.assert_not_called() # Should not attempt extraction for a duplicate

    # Verify no new message was added
    messages_count = db.session.query(WhatsAppMessage).filter_by(id='duplicate_msg_001').count()
    assert messages_count == 1

def test_process_message_missing_essential_fields_raises_error(app_context, test_app):
    payload_no_id = SAMPLE_TEXT_MESSAGE_PAYLOAD.copy()
    payload_no_id.pop('idMessage')
    with pytest.raises(ValueError, match="Essential WhatsApp message identifiers missing"):
        handle_new_whatsapp_message(payload_no_id, test_app)

    payload_no_chat_id = SAMPLE_TEXT_MESSAGE_PAYLOAD.copy()
    payload_no_chat_id['idMessage'] = 'msg_no_chat_id'
    payload_no_chat_id['senderData'] = payload_no_chat_id['senderData'].copy() # Ensure we modify a copy
    payload_no_chat_id['senderData'].pop('chatId')
    with pytest.raises(ValueError, match="Essential WhatsApp message identifiers missing"):
        handle_new_whatsapp_message(payload_no_chat_id, test_app)

@patch('app.background_tasks.extract_travel_data')
def test_process_media_message_with_caption(mock_extract_travel_data, app_context, test_app):
    mock_extract_travel_data.return_value = ({"first_name": "Jane", "last_name":"TicketHolder", "email": "[email protected]"}, "caption_extracted")
    payload = SAMPLE_MEDIA_MESSAGE_PAYLOAD.copy()
    payload['idMessage'] = 'media_test_01'

    result = handle_new_whatsapp_message(payload, test_app)

    assert result['status'] == 'success'
    wa_message = db.session.get(WhatsAppMessage, 'media_test_01')
    assert wa_message is not None
    assert wa_message.body == payload['messageData']['imageMessageData']['caption']
    assert wa_message.media_url == payload['messageData']['imageMessageData']['downloadUrl']
    assert wa_message.media_mime_type == payload['messageData']['imageMessageData']['mimeType']
    assert wa_message.media_filename == payload['messageData']['imageMessageData']['fileName']
    assert wa_message.message_type == 'imageMessage'

    mock_extract_travel_data.assert_called_once_with(payload['messageData']['imageMessageData']['caption'])
    extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=result['inquiry_id']).first()
    assert extracted_data is not None
    assert extracted_data.data['first_name'] == "Jane"
    assert extracted_data.validation_status == 'Complete'

    inquiry = db.session.get(Inquiry, result['inquiry_id'])
    assert inquiry is not None
    assert inquiry.status == 'Complete' # All essential fields mocked from caption

@patch('app.background_tasks.extract_travel_data')
def test_extraction_failure_incomplete_status(mock_extract_travel_data, app_context, test_app):
    # Mock extraction to return only some fields (not all essential)
    mock_extract_travel_data.return_value = ({"first_name": "Missing"}, "partial_extraction")
    payload = SAMPLE_TEXT_MESSAGE_PAYLOAD.copy()
    payload['idMessage'] = 'extraction_fail_01'
    payload['messageData']['textMessageData']['textMessage'] = "Only first name here"

    result = handle_new_whatsapp_message(payload, test_app)

    assert result['status'] == 'success'
    inquiry = db.session.get(Inquiry, result['inquiry_id'])
    assert inquiry.status == 'Incomplete' # Due to missing last_name and email

    extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=inquiry.id).first()
    assert extracted_data is not None
    assert extracted_data.data['first_name'] == "Missing"
    assert extracted_data.validation_status == 'Incomplete'
    assert "last_name" in extracted_data.missing_fields
    assert "email" in extracted_data.missing_fields

@patch('app.background_tasks.extract_travel_data')
def test_no_text_for_extraction_new_inquiry(mock_extract_travel_data, app_context, test_app):
    """Test a media message with no caption, for a new inquiry."""
    payload = SAMPLE_MEDIA_MESSAGE_PAYLOAD.copy()
    payload['idMessage'] = 'no_text_media_01'
    payload['messageData']['imageMessageData'].pop('caption', None) # Remove caption

    result = handle_new_whatsapp_message(payload, test_app)

    assert result['status'] == 'success'
    mock_extract_travel_data.assert_not_called() # No text content to extract

    inquiry = db.session.get(Inquiry, result['inquiry_id'])
    assert inquiry is not None
    assert inquiry.status == 'new_whatsapp' # Stays as new_whatsapp as no data extracted

    wa_message = db.session.get(WhatsAppMessage, 'no_text_media_01')
    assert wa_message.body is None
    assert wa_message.media_url is not None

    extracted_data_count = db.session.query(ExtractedData).filter_by(inquiry_id=inquiry.id).count()
    assert extracted_data_count == 0 # No ExtractedData record should be created

@patch('app.background_tasks.db.session.commit') # Mock commit to simulate DB error
@patch('app.background_tasks.extract_travel_data')
def test_db_integrity_error_other_than_duplicate_wa_message(mock_extract, mock_commit, app_context, test_app):
    from sqlalchemy.exc import IntegrityError
    # Simulate a generic IntegrityError during commit
    mock_commit.side_effect = IntegrityError("Mocked DB IntegrityError that isn't whatsapp_messages_pkey", MagicMock(), MagicMock())
    mock_extract.return_value = ({}, None) # Extraction returns nothing significant

    payload = SAMPLE_TEXT_MESSAGE_PAYLOAD.copy()
    payload['idMessage'] = 'db_error_test_01'

    with pytest.raises(IntegrityError):
        handle_new_whatsapp_message(payload, test_app)
    
    # Verify rollback was called (implicitly, as commit raised error, handler should rollback)
    # Hard to directly assert db.session.rollback() was called from here without more complex mocking
    # of the db object itself. The key is that the exception propagates.
    # And that no new Inquiry/WhatsAppMessage (that would cause the error) persists.

    assert db.session.query(Inquiry).count() == 0
    assert db.session.query(WhatsAppMessage).count() == 0

# Additional test ideas:
# - Merging data into existing ExtractedData for an existing inquiry.
# - Message from instance WID (from_me = True).
# - Different media types if parsing logic becomes more complex.
# - Payload with no messageData at all.

SAMPLE_MEDIA_MESSAGE_NO_CAPTION_PAYLOAD = {
    "typeWebhook": "incomingMessageReceived",
    "instanceData": {"idInstance": 789, "wid": "[email protected]", "typeInstance": "whatsapp"},
    "timestamp": int(datetime.now(timezone.utc).timestamp()),
    "idMessage": "testmedia_nocaption001",
    "senderData": {"chatId": "[email protected]", "sender": "[email protected]", "senderName": "No Caption User"},
    "messageData": {
        "typeMessage": "imageMessage",
        "imageMessageData": {
            "downloadUrl": "https://example.com/image_no_caption.jpg",
            "mimeType": "image/jpeg",
            # "caption": "This caption is intentionally missing",
            "fileName": "no_caption.jpg"
        }
    }
}

SAMPLE_STICKER_MESSAGE_PAYLOAD = {
    "typeWebhook": "incomingMessageReceived",
    "instanceData": {"idInstance": 101, "wid": "[email protected]", "typeInstance": "whatsapp"},
    "timestamp": int(datetime.now(timezone.utc).timestamp()),
    "idMessage": "teststicker001",
    "senderData": {"chatId": "[email protected]", "sender": "[email protected]", "senderName": "Sticker Sender"},
    "messageData": {
        "typeMessage": "stickerMessage",
        "stickerMessageData": {
            # Sticker specific data, might not be relevant for extraction logic
            "filehash": "sticker_file_hash_example" 
        }
    }
}


@patch('app.background_tasks.extract_travel_data')
def test_media_message_no_caption_existing_inquiry(mock_extract_travel_data, app_context, test_app):
    """Test media message with no caption for an existing inquiry."""
    payload = SAMPLE_MEDIA_MESSAGE_NO_CAPTION_PAYLOAD.copy()
    payload['idMessage'] = 'media_no_caption_existing_01'
    sender_chat_id = payload['senderData']['chatId']
    existing_inquiry_identifier = f"whatsapp_{sender_chat_id}@internal.placeholder"

    # Create existing Inquiry and ExtractedData
    existing_inquiry = Inquiry(primary_email_address=existing_inquiry_identifier, status='Complete')
    db.session.add(existing_inquiry)
    db.session.flush() # Get ID for ExtractedData
    
    initial_extracted_data = ExtractedData(
        inquiry_id=existing_inquiry.id,
        data={"first_name": "Original", "last_name": "Data"},
        validation_status="Complete"
    )
    db.session.add(initial_extracted_data)
    db.session.commit()
    db.session.refresh(existing_inquiry)
    db.session.refresh(initial_extracted_data)


    result = handle_new_whatsapp_message(payload, test_app)

    assert result['status'] == 'success'
    mock_extract_travel_data.assert_not_called() # No caption, so no extraction attempt

    wa_message = db.session.get(WhatsAppMessage, 'media_no_caption_existing_01')
    assert wa_message is not None
    assert wa_message.inquiry_id == existing_inquiry.id
    assert wa_message.body is None # No caption means body should be None
    assert wa_message.media_url == payload['messageData']['imageMessageData']['downloadUrl']
    assert wa_message.message_type == "imageMessage"

    updated_inquiry = db.session.get(Inquiry, existing_inquiry.id)
    assert updated_inquiry.status == 'Complete' # Status should not change

    updated_extracted_data = db.session.query(ExtractedData).filter_by(inquiry_id=existing_inquiry.id).first()
    assert updated_extracted_data is not None
    assert updated_extracted_data.data == {"first_name": "Original", "last_name": "Data"} # Data should not change
    assert updated_extracted_data.id == initial_extracted_data.id # Should be the same record


@patch('app.background_tasks.extract_travel_data')
def test_other_message_type_new_inquiry(mock_extract_travel_data, app_context, test_app):
    """Test a non-text, non-media message (e.g., sticker) for a new inquiry."""
    payload = SAMPLE_STICKER_MESSAGE_PAYLOAD.copy()
    payload['idMessage'] = 'sticker_new_inquiry_01'
    
    result = handle_new_whatsapp_message(payload, test_app)

    assert result['status'] == 'success'
    mock_extract_travel_data.assert_not_called()

    inquiry = db.session.get(Inquiry, result['inquiry_id'])
    assert inquiry is not None
    assert inquiry.status == 'new_whatsapp' # Default status for new inquiry with no extraction

    wa_message = db.session.get(WhatsAppMessage, 'sticker_new_inquiry_01')
    assert wa_message is not None
    assert wa_message.inquiry_id == inquiry.id
    assert wa_message.message_type == 'stickerMessage'
    assert wa_message.body is None # No text content

    extracted_data_count = db.session.query(ExtractedData).filter_by(inquiry_id=inquiry.id).count()
    assert extracted_data_count == 0


@patch('app.background_tasks.extract_travel_data')
def test_other_message_type_existing_inquiry(mock_extract_travel_data, app_context, test_app):
    """Test a non-text, non-media message (e.g., sticker) for an existing inquiry."""
    payload = SAMPLE_STICKER_MESSAGE_PAYLOAD.copy()
    payload['idMessage'] = 'sticker_existing_inquiry_01'
    sender_chat_id = payload['senderData']['chatId']
    existing_inquiry_identifier = f"whatsapp_{sender_chat_id}@internal.placeholder"

    # Create existing Inquiry
    existing_inquiry = Inquiry(primary_email_address=existing_inquiry_identifier, status='Incomplete')
    db.session.add(existing_inquiry)
    db.session.commit()
    db.session.refresh(existing_inquiry)

    result = handle_new_whatsapp_message(payload, test_app)

    assert result['status'] == 'success'
    assert result['inquiry_id'] == existing_inquiry.id
    mock_extract_travel_data.assert_not_called()

    updated_inquiry = db.session.get(Inquiry, existing_inquiry.id)
    assert updated_inquiry.status == 'Incomplete' # Status should remain unchanged

    wa_message = db.session.get(WhatsAppMessage, 'sticker_existing_inquiry_01')
    assert wa_message is not None
    assert wa_message.inquiry_id == existing_inquiry.id
    assert wa_message.message_type == 'stickerMessage'

    # Ensure no new ExtractedData was created or existing one modified (if we had one)
    extracted_data_count = db.session.query(ExtractedData).filter_by(inquiry_id=existing_inquiry.id).count()
    assert extracted_data_count == 0