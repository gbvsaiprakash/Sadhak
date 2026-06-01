"""
Tests for recurring task syncing to Google Calendar.
Run with: python manage.py test integrations.tests.test_recurring_sync
"""

import logging
import uuid
from datetime import datetime, date
from django.test import TestCase
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
from integrations.services import create_recurring_google_event
from integrations.models import EventSyncMap, GoogleCalendarConnection
from tracker.models import Task
from integrations.rrule_handler import RRuleHandler

logger = logging.getLogger(__name__)
User = get_user_model()


class RecurringTaskSyncTests(TestCase):
    """Test syncing recurring tasks to Google Calendar."""
    
    def setUp(self):
        """Set up test user and Google Calendar connection."""
        self.user = User.objects.create(
            username="testuser",
            email="test@example.com",
        )
        
        # Create a mock Google Calendar connection
        self.connection = GoogleCalendarConnection.objects.create(
            user=self.user,
            email="user@gmail.com",
            google_sub="google_123",
            refresh_token="encrypted_refresh_token",
            access_token="encrypted_access_token",
            token_expiry=datetime.now(),
            scope="calendar",
            is_active=True,
        )
    
    @patch('integrations.services._google_request_json')
    @patch('integrations.services.ensure_valid_access_token')
    def test_create_recurring_google_event_daily(self, mock_token, mock_request):
        """Test creating a daily recurring event in Google Calendar."""
        mock_token.return_value = "access_token"
        mock_request.return_value = {
            "id": "google_event_123",
            "etag": "etag_123",
        }
        
        # Create task with daily recurrence
        task = Task.objects.create(
            user=self.user,
            title="Daily Standup",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            start_time=datetime.strptime("09:00:00", "%H:%M:%S").time(),
            end_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20241231",
        )
        
        # Create recurring event
        result = create_recurring_google_event(self.user, task)
        
        self.assertTrue(result["created"])
        self.assertEqual(result["google_event_id"], "google_event_123")
        
        # Verify task was updated with google_event_id
        task.refresh_from_db()
        self.assertEqual(task.google_event_id, "google_event_123")
        
        # Verify EventSyncMap was created
        mapping = EventSyncMap.objects.get(local_task_id=task.id)
        self.assertTrue(mapping.is_recurring)
        self.assertEqual(mapping.recurrence_rule, task.recurrence_rule)
        logger.info(f"Created recurring event: {result}")
    
    @patch('integrations.services._google_request_json')
    @patch('integrations.services.ensure_valid_access_token')
    def test_create_recurring_google_event_weekly(self, mock_token, mock_request):
        """Test creating a weekly recurring event (Mon, Wed, Fri)."""
        mock_token.return_value = "access_token"
        mock_request.return_value = {
            "id": "google_event_456",
            "etag": "etag_456",
        }
        
        # Create weekly recurrence (Mon, Wed, Fri)
        rrule = RRuleHandler.build_rrule(
            frequency_type="weekly",
            start_date=datetime(2024, 1, 1, 10, 0),
            frequency_days="0,2,4",
            end_date=datetime(2024, 12, 31),
        )
        
        task = Task.objects.create(
            user=self.user,
            title="Weekly Team Meeting",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            start_time=datetime.strptime("14:00:00", "%H:%M:%S").time(),
            end_time=datetime.strptime("15:00:00", "%H:%M:%S").time(),
            recurrence_rule=rrule,
        )
        
        result = create_recurring_google_event(self.user, task)
        
        self.assertTrue(result["created"])
        self.assertEqual(result["google_event_id"], "google_event_456")
        
        # Verify Google API was called with correct recurrence
        self.assertTrue(mock_request.called)
        call_args = mock_request.call_args
        payload = call_args[0][3]  # 4th argument is the payload
        
        self.assertIn("recurrence", payload)
        self.assertIsInstance(payload["recurrence"], list)
        self.assertTrue(payload["recurrence"][0].startswith("RRULE:"))
        logger.info(f"Weekly recurring event created: {payload['recurrence']}")
    
    @patch('integrations.services._google_request_json')
    @patch('integrations.services.ensure_valid_access_token')
    def test_create_recurring_google_event_monthly(self, mock_token, mock_request):
        """Test creating a monthly recurring event."""
        mock_token.return_value = "access_token"
        mock_request.return_value = {
            "id": "google_event_789",
            "etag": "etag_789",
        }
        
        # Create monthly recurrence on the 15th
        rrule = RRuleHandler.build_rrule(
            frequency_type="monthly",
            start_date=datetime(2024, 1, 15, 10, 0),
            day_of_month=15,
            end_date=datetime(2024, 12, 31),
        )
        
        task = Task.objects.create(
            user=self.user,
            title="Monthly Review",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 15),
            end_date=date(2024, 12, 31),
            start_time=datetime.strptime("18:00:00", "%H:%M:%S").time(),
            recurrence_rule=rrule,
        )
        
        result = create_recurring_google_event(self.user, task)
        
        self.assertTrue(result["created"])
        self.assertIn("BYMONTHDAY=15", task.recurrence_rule)
        logger.info(f"Monthly recurring event created: {rrule}")
    
    def test_create_recurring_event_without_recurrence_rule(self):
        """Test that tasks without recurrence_rule are skipped."""
        task = Task.objects.create(
            user=self.user,
            title="One-off Task",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 1),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            # No recurrence_rule
        )
        
        result = create_recurring_google_event(self.user, task)
        
        self.assertFalse(result["created"])
        self.assertIn("no recurrence_rule", result["error"])
    
    def test_create_recurring_event_no_google_connection(self):
        """Test that sync fails gracefully without Google Calendar connection."""
        # Create user without Google Calendar connection
        user_no_google = User.objects.create(
            username="nogoogle",
            email="nogoogle@example.com",
        )
        
        task = Task.objects.create(
            user=user_no_google,
            title="Recurring Task",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 1),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY",
        )
        
        result = create_recurring_google_event(user_no_google, task)
        
        self.assertFalse(result["created"])
        self.assertIn("not connected", result["error"])
        logger.info("Gracefully handled missing Google Calendar connection")


class EventSyncMapRecurringTests(TestCase):
    """Test EventSyncMap for recurring events."""
    
    def setUp(self):
        self.user = User.objects.create(
            username="testuser",
            email="test@example.com",
        )
    
    def test_event_sync_map_recurring_fields(self):
        """Test that EventSyncMap correctly stores recurring event metadata."""
        task_id = uuid.uuid4()
        mapping = EventSyncMap.objects.create(
            user=self.user,
            local_task_id=task_id,
            local_occurrence_id=task_id,
            local_parent_type="task",
            google_event_id="google_event_123",
            calendar_id="primary",
            is_recurring=True,
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20241231",
            google_etag="etag_123",
            is_deleted=False,
        )
        
        # Verify fields are stored correctly
        self.assertTrue(mapping.is_recurring)
        self.assertEqual(mapping.recurrence_rule, "FREQ=DAILY;INTERVAL=1;UNTIL=20241231")
        self.assertEqual(mapping.google_etag, "etag_123")
        self.assertEqual(mapping.local_task_id, task_id)
        logger.info(f"EventSyncMap created for recurring task: {mapping.id}")
    
    def test_distinguish_recurring_from_single(self):
        """Test that we can distinguish recurring events from single events."""
        task_id = uuid.uuid4()
        occurrence_id = uuid.uuid4()
        
        # Single event
        single = EventSyncMap.objects.create(
            user=self.user,
            local_occurrence_id=occurrence_id,
            local_parent_type="task",
            google_event_id="google_single",
            calendar_id="primary",
            is_recurring=False,
        )
        
        # Recurring event
        recurring = EventSyncMap.objects.create(
            user=self.user,
            local_task_id=task_id,
            local_occurrence_id=task_id,
            local_parent_type="task",
            google_event_id="google_recurring",
            calendar_id="primary",
            is_recurring=True,
            recurrence_rule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
        )
        
        # Query recurring events
        recurring_maps = EventSyncMap.objects.filter(is_recurring=True)
        self.assertEqual(recurring_maps.count(), 1)
        self.assertEqual(recurring_maps.first().google_event_id, "google_recurring")
        
        # Query single events
        single_maps = EventSyncMap.objects.filter(is_recurring=False)
        self.assertGreaterEqual(single_maps.count(), 1)
        logger.info("Successfully distinguished recurring and single event mappings")
