import unittest
from unittest.mock import patch, MagicMock, ANY
from datetime import datetime, timezone, timedelta

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Assuming Flask app and other necessary modules are importable
# This setup can be complex depending on the Flask app structure (app factory, etc.)
# For this example, we'll mock heavily and assume `app` can be imported or constructed.

# Attempt to import necessary components
# These imports might need significant adjustment based on actual project structure
# and how the Flask app context is managed in tests.
try:
    from app.background_tasks import poll_new_emails, get_email_queue # For patching get_email_queue
    from web_app import app as flask_app # Assuming your Flask app instance is here
except ImportError as e:
    print(f"Test setup ImportError: {e}. You might need to adjust PYTHONPATH or test structure.")
    # Define placeholders if imports fail, so the file can be parsed
    flask_app = MagicMock()
    def poll_new_emails(app_param):
        pass
    def get_email_queue():
        return MagicMock()

# Helper to create email summaries for testing
def create_ms_email_summary(email_id, received_datetime_str):
    return {
        'id': email_id,
        'subject': f'Test Subject {email_id}',
        'receivedDateTime': received_datetime_str,
        'bodyPreview': 'Test preview'
        # Add other fields as used by poll_new_emails or classify_email_intent
    }

class TestPollNewEmailsIntegration(unittest.TestCase):

    def setUp(self):
        # It's often good to use a test-specific configuration for Flask app in tests
        # flask_app.config['TESTING'] = True
        # flask_app.config['REDIS_URL'] = 'redis://localhost:6379/1' # Test Redis DB
        # For simplicity, we'll rely on patching heavily.
        self.app_context = flask_app.app_context()
        self.app_context.push() # Push an app context for the duration of the test
        
        # Reset or manage global state like last_checked_timestamp if it's in background_tasks
        # This might require making `last_checked_timestamp` more testable (e.g., part of a class)
        # For now, we'll assume it can be patched or it resets appropriately.
        patcher = patch('app.background_tasks.last_checked_timestamp', None)
        self.mock_last_checked = patcher.start()
        self.addCleanup(patcher.stop)


    def tearDown(self):
        if self.app_context:
            self.app_context.pop()

    @patch('app.background_tasks.get_email_queue') # Mocks the function that returns the queue
    @patch('app.background_tasks.classify_email_intent')
    @patch('app.background_tasks.fetch_new_emails_since') # Mock the MS Graph service call
    def test_poll_no_new_emails(self, mock_fetch_emails, mock_classify, mock_get_queue):
        """Test poll_new_emails when fetch_new_emails_since returns an empty list."""
        mock_fetch_emails.return_value = []
        mock_rq_queue = MagicMock()
        mock_get_queue.return_value = mock_rq_queue
        
        poll_new_emails(flask_app) # Pass the actual app or a suitable mock
        
        mock_fetch_emails.assert_called_once_with(ANY) # ANY for timestamp
        mock_classify.assert_not_called()
        mock_rq_queue.enqueue.assert_not_called()
        self.assertIsNotNone(self.mock_last_checked) # Check that last_checked_timestamp was updated

    @patch('app.background_tasks.get_email_queue')
    @patch('app.background_tasks.classify_email_intent')
    @patch('app.background_tasks.fetch_new_emails_since')
    def test_poll_single_email_enqueued(self, mock_fetch_emails, mock_classify, mock_get_queue):
        """Test polling and successfully enqueueing a single email."""
        email_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        email_summary = create_ms_email_summary("email123", email_time.isoformat())
        mock_fetch_emails.return_value = [email_summary]
        mock_classify.return_value = "test_intent"
        
        mock_rq_queue = MagicMock()
        mock_get_queue.return_value = mock_rq_queue
        
        poll_new_emails(flask_app)
        
        mock_fetch_emails.assert_called_once()
        mock_classify.assert_called_once_with(email_summary['subject'], email_summary['bodyPreview'])
        mock_rq_queue.enqueue.assert_called_once_with(
            'app.background_tasks.process_email_job',
            email_summary,
            "test_intent",
            job_timeout=ANY,
            result_ttl=ANY
        )
        self.assertIsNotNone(self.mock_last_checked)

    @patch('app.background_tasks.get_email_queue')
    @patch('app.background_tasks.classify_email_intent')
    @patch('app.background_tasks.fetch_new_emails_since')
    def test_poll_multiple_emails_enqueued(self, mock_fetch_emails, mock_classify, mock_get_queue):
        """Test polling and enqueueing multiple emails."""
        emails_data = [
            create_ms_email_summary("email001", (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()),
            create_ms_email_summary("email002", (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat())
        ]
        mock_fetch_emails.return_value = emails_data
        mock_classify.side_effect = ["intent1", "intent2"]
        
        mock_rq_queue = MagicMock()
        mock_get_queue.return_value = mock_rq_queue
        
        poll_new_emails(flask_app)
        
        self.assertEqual(mock_classify.call_count, 2)
        self.assertEqual(mock_rq_queue.enqueue.call_count, 2)
        # Check call args for the first one as an example
        mock_rq_queue.enqueue.assert_any_call(
            'app.background_tasks.process_email_job',
            emails_data[0],
            "intent1",
            job_timeout=ANY,
            result_ttl=ANY
        )
        self.assertIsNotNone(self.mock_last_checked)

    @patch('app.background_tasks.get_email_queue')
    @patch('app.background_tasks.classify_email_intent')
    @patch('app.background_tasks.fetch_new_emails_since')
    def test_fetch_emails_fails_timestamp_not_updated(self, mock_fetch_emails, mock_classify, mock_get_queue):
        """Test that if fetch_new_emails_since raises an exception, timestamp is not updated."""
        mock_fetch_emails.side_effect = Exception("MS Graph API Down")
        mock_rq_queue = MagicMock()
        mock_get_queue.return_value = mock_rq_queue
        
        # Clear the mock_last_checked before the call to see if it gets set
        self.mock_last_checked = None 
        # We need to re-patch it for this test case to set it to None initially before the call
        with patch('app.background_tasks.last_checked_timestamp', None) as self.mock_last_checked_in_test:
            with self.assertLogs(level='ERROR') as cm:
                 poll_new_emails(flask_app)
                 self.assertIn("Error during email polling cycle: MS Graph API Down", cm.output[0])
            
            mock_classify.assert_not_called()
            mock_rq_queue.enqueue.assert_not_called()
            self.assertIsNone(self.mock_last_checked_in_test) # Timestamp should NOT have been updated

    @patch('app.background_tasks.get_email_queue')
    @patch('app.background_tasks.classify_email_intent')
    @patch('app.background_tasks.fetch_new_emails_since')
    def test_classification_fails_skips_email(self, mock_fetch_emails, mock_classify, mock_get_queue):
        """Test that if classification fails for one email, it's skipped and others are processed."""
        emails_data = [
            create_ms_email_summary("email_fail", (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()),
            create_ms_email_summary("email_ok", (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat())
        ]
        mock_fetch_emails.return_value = emails_data
        mock_classify.side_effect = [Exception("Classification failed!"), "intent_ok"]
        
        mock_rq_queue = MagicMock()
        mock_get_queue.return_value = mock_rq_queue
        
        with self.assertLogs(level='ERROR') as cm:
            poll_new_emails(flask_app)
            self.assertIn("Failed to classify intent for email_fail: Classification failed!", cm.output[0])

        self.assertEqual(mock_classify.call_count, 2)
        mock_rq_queue.enqueue.assert_called_once_with(
            'app.background_tasks.process_email_job',
            emails_data[1], # Only the second email should be enqueued
            "intent_ok",
            job_timeout=ANY,
            result_ttl=ANY
        )
        self.assertIsNotNone(self.mock_last_checked) # Timestamp updated as the poll cycle itself succeeded

    @patch('app.background_tasks.get_email_queue')
    @patch('app.background_tasks.classify_email_intent')
    @patch('app.background_tasks.fetch_new_emails_since')
    def test_enqueue_fails_skips_email_and_logs(self, mock_fetch_emails, mock_classify, mock_get_queue):
        """Test that if enqueuing fails for one email, it's skipped and others are processed."""
        import redis # For redis.exceptions.ConnectionError
        emails_data = [
            create_ms_email_summary("email_enqueue_fail", (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()),
            create_ms_email_summary("email_enqueue_ok", (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat())
        ]
        mock_fetch_emails.return_value = emails_data
        mock_classify.side_effect = ["intent_fail_enqueue", "intent_ok_enqueue"]
        
        mock_rq_queue = MagicMock()
        # Simulate ConnectionError on the first call to enqueue, success on the second
        mock_rq_queue.enqueue.side_effect = [redis.exceptions.ConnectionError("Redis down!"), MagicMock()]
        mock_get_queue.return_value = mock_rq_queue

        with self.assertLogs(level='ERROR') as cm:
            poll_new_emails(flask_app)
            self.assertIn("Redis connection error: Redis down!. Skipping email email_enqueue_fail.", cm.output[0])
        
        self.assertEqual(mock_classify.call_count, 2)
        self.assertEqual(mock_rq_queue.enqueue.call_count, 2)
        # Check that the second email was enqueued correctly
        # The first call to enqueue raised an error, the second one should have worked.
        # We can inspect call_args_list, the successful call would be the one that didn't raise.
        # For simplicity, we're checking that enqueue was called twice and an error logged for the first.
        # A more precise check would verify the arguments of the *successful* call.
        
        # Assert that the second email (index 1) was attempted with its corresponding intent
        args_list = mock_rq_queue.enqueue.call_args_list
        self.assertEqual(args_list[1][0][0], 'app.background_tasks.process_email_job')
        self.assertEqual(args_list[1][0][1], emails_data[1])
        self.assertEqual(args_list[1][0][2], "intent_ok_enqueue")

        self.assertIsNotNone(self.mock_last_checked)

if __name__ == '__main__':
    unittest.main() 