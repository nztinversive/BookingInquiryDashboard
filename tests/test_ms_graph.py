import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import copy # For deepcopying mock data

# Assuming ms_graph_service.py is in the parent directory or accessible via PYTHONPATH
# For this example, let's assume it's one level up.
# You might need to adjust imports based on your project structure and how you run tests.
# Example: from ..ms_graph_service import fetch_new_emails_since, _make_graph_api_call, configure_ms_graph_client
# For simplicity in this generated example, we'll assume it can be imported directly if tests are run from the root.
# This often requires adding the project root to sys.path or using a test runner that handles this.

# To make this runnable, we might need to add the project root to sys.path
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ms_graph_service import fetch_new_emails_since, configure_ms_graph_client, _graph_config

# Sample config for testing
TEST_CONFIG = {
    "MS_GRAPH_CLIENT_ID": "test_client_id",
    "MS_GRAPH_CLIENT_SECRET": "test_client_secret",
    "MS_GRAPH_TENANT_ID": "test_tenant_id",
    "MS_GRAPH_MAILBOX_USER_ID": "test_user_id"
}

# Helper to create email summaries
def create_email_summary(email_id, received_datetime_str):
    return {
        "id": email_id,
        "subject": f"Test Email {email_id}",
        "receivedDateTime": received_datetime_str,
        "isRead": False,
        "from": {"emailAddress": {"address": "sender@example.com", "name": "Sender"}},
        "bodyPreview": "This is a test email body preview."
    }

class TestFetchNewEmailsSince(unittest.TestCase):

    def setUp(self):
        # Configure the client for each test to ensure a clean state
        # Make a deep copy of _graph_config to avoid altering the original during tests if it were complex
        self.original_graph_config = copy.deepcopy(_graph_config)
        configure_ms_graph_client(MagicMock(**TEST_CONFIG))

    def tearDown(self):
        # Restore original config if necessary, or clear it
        global _graph_config
        _graph_config = self.original_graph_config
        # Or, if configure_ms_graph_client resets it properly on bad/empty input:
        # configure_ms_graph_client({}) 

    @patch('ms_graph_service._make_graph_api_call')
    def test_fetch_no_new_emails(self, mock_make_call):
        """Test scenario where no new emails are returned by the API."""
        mock_make_call.return_value = {"value": []}
        
        timestamp = datetime.now(timezone.utc) - timedelta(days=1)
        emails = fetch_new_emails_since(timestamp)
        
        self.assertEqual(len(emails), 0)
        mock_make_call.assert_called_once()
        # We can add more assertions on the call arguments if needed

    @patch('ms_graph_service._make_graph_api_call')
    def test_fetch_single_page_less_than_top(self, mock_make_call):
        """Test scenario with one page of emails, less than the $top limit."""
        mock_email_data = [
            create_email_summary("email1", "2023-01-01T10:00:00Z"),
            create_email_summary("email2", "2023-01-01T10:05:00Z")
        ]
        mock_make_call.return_value = {"value": mock_email_data}
        
        timestamp = datetime.now(timezone.utc) - timedelta(days=1)
        emails = fetch_new_emails_since(timestamp)
        
        self.assertEqual(len(emails), 2)
        self.assertEqual(emails[0]["id"], "email1")
        self.assertEqual(emails[1]["id"], "email2")
        mock_make_call.assert_called_once()

    @patch('ms_graph_service._make_graph_api_call')
    def test_fetch_multiple_pages(self, mock_make_call):
        """Test scenario with multiple pages of emails."""
        page1_emails = [create_email_summary(f"p1_email{i}", f"2023-01-01T10:{i:02d}:00Z") for i in range(2)] # 2 emails
        page2_emails = [create_email_summary(f"p2_email{i}", f"2023-01-01T10:{i+2:02d}:00Z") for i in range(1)] # 1 email

        # Configure side_effect to return different values for each call
        mock_make_call.side_effect = [
            {
                "value": page1_emails,
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/users/test_user_id/messages?next_page_token_1"
            },
            {
                "value": page2_emails
                # No nextLink on the last page
            }
        ]
        
        timestamp = datetime.now(timezone.utc) - timedelta(days=1)
        emails = fetch_new_emails_since(timestamp)
        
        self.assertEqual(len(emails), 3) # 2 from page1 + 1 from page2
        self.assertEqual(emails[0]["id"], "p1_email0")
        self.assertEqual(emails[1]["id"], "p1_email1")
        self.assertEqual(emails[2]["id"], "p2_email0")
        
        self.assertEqual(mock_make_call.call_count, 2)
        # First call with initial params
        first_call_args = mock_make_call.call_args_list[0]
        self.assertIn('$filter', first_call_args[1]['params']) 
        # Second call with nextLink URL and no explicit params
        second_call_args = mock_make_call.call_args_list[1]
        self.assertEqual(second_call_args[0][1], "https://graph.microsoft.com/v1.0/users/test_user_id/messages?next_page_token_1")
        self.assertIsNone(second_call_args[1]['params'])


    @patch('ms_graph_service._make_graph_api_call')
    def test_api_call_failure_returns_empty_list(self, mock_make_call):
        """Test that an API call failure results in an empty list and logs an error."""
        mock_make_call.side_effect = Exception("Simulated API Error")
        
        timestamp = datetime.now(timezone.utc) - timedelta(days=1)
        
        # Check if logging.error was called (more advanced, might need to patch logging)
        with self.assertLogs('ms_graph_service', level='ERROR') as cm:
            emails = fetch_new_emails_since(timestamp)
            self.assertIn("Error polling new emails: Simulated API Error", cm.output[0])

        self.assertEqual(len(emails), 0)
        mock_make_call.assert_called_once()

    @patch('ms_graph_service._make_graph_api_call')
    def test_empty_value_in_response_first_page(self, mock_make_call):
        """Test scenario where first page returns empty value but has a nextLink."""
        page2_emails = [create_email_summary("p2_email0", "2023-01-01T10:00:00Z")]
        mock_make_call.side_effect = [
            {
                "value": [], # Empty first page
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/users/test_user_id/messages?next_page_token_empty"
            },
            {
                "value": page2_emails
            }
        ]
        timestamp = datetime.now(timezone.utc) - timedelta(days=1)
        emails = fetch_new_emails_since(timestamp)
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["id"], "p2_email0")
        self.assertEqual(mock_make_call.call_count, 2)

    @patch('ms_graph_service._make_graph_api_call')
    def test_no_value_field_in_response(self, mock_make_call):
        """Test scenario where API response is missing the 'value' field."""
        mock_make_call.return_value = {"@odata.nextLink": "some_link"} # No 'value'
        
        timestamp = datetime.now(timezone.utc) - timedelta(days=1)
        emails = fetch_new_emails_since(timestamp)
        
        self.assertEqual(len(emails), 0)
        # It should still try the nextLink if present
        mock_make_call.side_effect = [
            {"@odata.nextLink": "some_link"}, # Page 1, no value
            {"value": [create_email_summary("email1", "2023-01-01T10:00:00Z")]} # Page 2
        ]
        emails = fetch_new_emails_since(timestamp)
        self.assertEqual(len(emails), 1)
        self.assertEqual(mock_make_call.call_count, 2)


if __name__ == '__main__':
    unittest.main() 