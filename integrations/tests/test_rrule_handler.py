"""
Tests for RRULE handler functionality.
Run with: python manage.py test integrations.tests.test_rrule_handler
"""

import logging
from datetime import datetime, timedelta
from django.test import TestCase
from integrations.rrule_handler import (
    RRuleHandler,
    build_daily_rrule,
    build_weekly_rrule,
    build_monthly_rrule,
    build_yearly_rrule,
)

logger = logging.getLogger(__name__)


class RRuleHandlerBuildTests(TestCase):
    """Test RRULE building from app frequency parameters."""
    
    def test_build_daily_rrule(self):
        """Test building a simple daily RRULE."""
        start = datetime(2024, 1, 1, 10, 0)
        end = datetime(2024, 12, 31)
        
        rrule = RRuleHandler.build_rrule(
            frequency_type="daily",
            start_date=start,
            frequency_interval=1,
            end_date=end,
        )
        
        self.assertIn("FREQ=DAILY", rrule)
        # INTERVAL=1 is omitted by default (it's the default)
        self.assertIn("UNTIL=20241231", rrule)
        logger.info(f"Daily RRULE: {rrule}")
    
    def test_build_daily_rrule_with_interval(self):
        """Test building daily RRULE with interval > 1."""
        start = datetime(2024, 1, 1)
        end = datetime(2024, 12, 31)
        
        rrule = RRuleHandler.build_rrule(
            frequency_type="daily",
            start_date=start,
            frequency_interval=3,
            end_date=end,
        )
        
        self.assertIn("FREQ=DAILY", rrule)
        self.assertIn("INTERVAL=3", rrule)
        logger.info(f"Daily RRULE (interval 3): {rrule}")
    
    def test_build_weekly_rrule(self):
        """Test building a weekly RRULE with specific weekdays."""
        start = datetime(2024, 1, 1)  # Monday
        end = datetime(2024, 12, 31)
        
        # Monday (0), Wednesday (2), Friday (4)
        rrule = RRuleHandler.build_rrule(
            frequency_type="weekly",
            start_date=start,
            frequency_days="0,2,4",
            end_date=end,
        )
        
        self.assertIn("FREQ=WEEKLY", rrule)
        self.assertIn("BYDAY=MO,WE,FR", rrule)
        self.assertIn("UNTIL=20241231", rrule)
        logger.info(f"Weekly RRULE (MWF): {rrule}")
    
    def test_build_weekly_rrule_single_day(self):
        """Test building a weekly RRULE for a single day."""
        start = datetime(2024, 1, 1)  # Monday
        end = datetime(2024, 12, 31)
        
        rrule = RRuleHandler.build_rrule(
            frequency_type="weekly",
            start_date=start,
            day_of_week=0,  # Monday
            end_date=end,
        )
        
        self.assertIn("FREQ=WEEKLY", rrule)
        self.assertIn("BYDAY=MO", rrule)
        logger.info(f"Weekly RRULE (Monday only): {rrule}")
    
    def test_build_monthly_rrule(self):
        """Test building a monthly RRULE."""
        start = datetime(2024, 1, 15)
        end = datetime(2024, 12, 31)
        
        rrule = RRuleHandler.build_rrule(
            frequency_type="monthly",
            start_date=start,
            day_of_month=15,
            end_date=end,
        )
        
        self.assertIn("FREQ=MONTHLY", rrule)
        self.assertIn("BYMONTHDAY=15", rrule)
        self.assertIn("UNTIL=20241231", rrule)
        logger.info(f"Monthly RRULE: {rrule}")
    
    def test_build_yearly_rrule(self):
        """Test building a yearly RRULE."""
        start = datetime(2024, 1, 1)
        end = datetime(2026, 12, 31)
        
        rrule = RRuleHandler.build_rrule(
            frequency_type="yearly",
            start_date=start,
            end_date=end,
        )
        
        self.assertIn("FREQ=YEARLY", rrule)
        self.assertIn("UNTIL=20261231", rrule)
        logger.info(f"Yearly RRULE: {rrule}")
    
    def test_build_rrule_invalid_frequency(self):
        """Test that invalid frequency_type raises ValueError."""
        with self.assertRaises(ValueError):
            RRuleHandler.build_rrule(
                frequency_type="invalid",
                start_date=datetime.now(),
            )


class RRuleHandlerParseTests(TestCase):
    """Test RRULE parsing functionality."""
    
    def test_parse_simple_daily_rrule(self):
        """Test parsing a simple daily RRULE."""
        rrule_str = "FREQ=DAILY;INTERVAL=1;UNTIL=20241231"
        parsed = RRuleHandler.parse_rrule(rrule_str)
        
        self.assertEqual(parsed["frequency_type"], "daily")
        self.assertEqual(parsed["frequency_interval"], 1)
        self.assertEqual(parsed["until_date"], datetime(2024, 12, 31))
        logger.info(f"Parsed daily RRULE: {parsed}")
    
    def test_parse_weekly_rrule(self):
        """Test parsing a weekly RRULE."""
        rrule_str = "FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20241231"
        parsed = RRuleHandler.parse_rrule(rrule_str)
        
        self.assertEqual(parsed["frequency_type"], "weekly")
        self.assertIn("MO", parsed["byday"])
        self.assertIn("WE", parsed["byday"])
        self.assertIn("FR", parsed["byday"])
        logger.info(f"Parsed weekly RRULE: {parsed}")
    
    def test_parse_monthly_rrule(self):
        """Test parsing a monthly RRULE."""
        rrule_str = "FREQ=MONTHLY;BYMONTHDAY=15;UNTIL=20241231"
        parsed = RRuleHandler.parse_rrule(rrule_str)
        
        self.assertEqual(parsed["frequency_type"], "monthly")
        self.assertEqual(parsed["bymonthday"], 15)
        logger.info(f"Parsed monthly RRULE: {parsed}")
    
    def test_parse_yearly_rrule(self):
        """Test parsing a yearly RRULE."""
        rrule_str = "FREQ=YEARLY;UNTIL=20261231"
        parsed = RRuleHandler.parse_rrule(rrule_str)
        
        self.assertEqual(parsed["frequency_type"], "yearly")
        logger.info(f"Parsed yearly RRULE: {parsed}")
    
    def test_parse_invalid_rrule(self):
        """Test that malformed RRULE is handled gracefully."""
        # Note: parse_rrule is lenient and extracts what it can
        parsed = RRuleHandler.parse_rrule("INVALID;RRULE;FORMAT")
        # Should return empty dict or partial results, not raise
        self.assertIsInstance(parsed, dict)
        logger.info(f"Parsed invalid RRULE (lenient): {parsed}")


class RRuleHandlerExpandTests(TestCase):
    """Test RRULE expansion into individual occurrences."""
    
    def test_expand_daily_rrule(self):
        """Test expanding a daily RRULE."""
        start = datetime(2024, 1, 1, 10, 0)
        rrule_str = "FREQ=DAILY;INTERVAL=1"
        
        occurrences = RRuleHandler.expand_rrule(
            rrule_str,
            start_date=start,
            count=5,
        )
        
        self.assertEqual(len(occurrences), 5)
        # Each day should be 24 hours apart
        self.assertEqual(occurrences[1] - occurrences[0], timedelta(days=1))
        logger.info(f"Daily occurrences: {[o.date() for o in occurrences]}")
    
    def test_expand_daily_rrule_with_interval(self):
        """Test expanding daily RRULE with interval."""
        start = datetime(2024, 1, 1, 10, 0)
        rrule_str = "FREQ=DAILY;INTERVAL=2"
        
        occurrences = RRuleHandler.expand_rrule(
            rrule_str,
            start_date=start,
            count=5,
        )
        
        self.assertEqual(len(occurrences), 5)
        # Each occurrence should be 2 days apart
        self.assertEqual(occurrences[1] - occurrences[0], timedelta(days=2))
        logger.info(f"Daily (interval 2) occurrences: {[o.date() for o in occurrences]}")
    
    def test_expand_weekly_rrule(self):
        """Test expanding a weekly RRULE."""
        # Start on Monday, 2024-01-01
        start = datetime(2024, 1, 1, 10, 0)
        # Monday, Wednesday, Friday
        rrule_str = "FREQ=WEEKLY;BYDAY=MO,WE,FR"
        
        occurrences = RRuleHandler.expand_rrule(
            rrule_str,
            start_date=start,
            count=6,  # 2 weeks
        )
        
        self.assertEqual(len(occurrences), 6)
        # First 3 should be Mon, Wed, Fri of week 1
        logger.info(f"Weekly (MWF) occurrences: {[o.strftime('%A %Y-%m-%d') for o in occurrences]}")
    
    def test_expand_monthly_rrule(self):
        """Test expanding a monthly RRULE."""
        start = datetime(2024, 1, 15, 10, 0)
        rrule_str = "FREQ=MONTHLY;BYMONTHDAY=15"
        
        occurrences = RRuleHandler.expand_rrule(
            rrule_str,
            start_date=start,
            count=5,
        )
        
        self.assertEqual(len(occurrences), 5)
        # All should be on the 15th
        for occ in occurrences:
            self.assertEqual(occ.day, 15)
        logger.info(f"Monthly occurrences: {[o.strftime('%Y-%m-%d') for o in occurrences]}")
    
    def test_expand_yearly_rrule(self):
        """Test expanding a yearly RRULE."""
        start = datetime(2024, 1, 1, 10, 0)
        # Need to specify UNTIL to get multiple occurrences
        rrule_str = "FREQ=YEARLY;UNTIL=20261231"
        
        occurrences = RRuleHandler.expand_rrule(
            rrule_str,
            start_date=start,
            count=3,
        )
        
        # Should get Jan 1 of 2024, 2025, 2026
        self.assertEqual(len(occurrences), 3)
        # Each year apart
        self.assertEqual(occurrences[1].year - occurrences[0].year, 1)
        logger.info(f"Yearly occurrences: {[o.strftime('%Y-%m-%d') for o in occurrences]}")
    
    def test_expand_with_end_date(self):
        """Test expanding RRULE with end_date override."""
        start = datetime(2024, 1, 1, 10, 0)
        end = datetime(2024, 1, 15, 23, 59)  # Include entire Jan 15
        rrule_str = "FREQ=DAILY"
        
        occurrences = RRuleHandler.expand_rrule(
            rrule_str,
            start_date=start,
            end_date=end,
            count=100,  # Ask for more than available
        )
        
        # Should have 15 days (Jan 1-15 inclusive)
        self.assertEqual(len(occurrences), 15)
        self.assertEqual(occurrences[-1].date(), datetime(2024, 1, 15).date())
        logger.info(f"Daily (limited by end_date): {len(occurrences)} occurrences")


class RRuleHandlerNextOccurrenceTests(TestCase):
    """Test getting next occurrence."""
    
    def test_get_next_occurrence(self):
        """Test getting the next occurrence after a date."""
        start = datetime(2024, 1, 1, 10, 0)
        rrule_str = "FREQ=DAILY"
        after = datetime(2024, 1, 5, 10, 0)  # Exact time
        
        next_occ = RRuleHandler.get_next_occurrence(
            rrule_str,
            start_date=start,
            after_date=after,
        )
        
        # Should be Jan 6 (first occurrence strictly after Jan 5 10:00 AM)
        self.assertEqual(next_occ.date(), datetime(2024, 1, 6).date())
        logger.info(f"Next occurrence after {after.date()}: {next_occ}")


class RRuleHandlerGoogleConversionTests(TestCase):
    """Test conversion to/from Google Calendar format."""
    
    def test_rrule_to_google_format(self):
        """Test converting RRULE to Google Calendar format."""
        rrule_str = "FREQ=DAILY;INTERVAL=1;UNTIL=20241231"
        
        google_recurrence = RRuleHandler.rrule_to_google_event_recurrence(rrule_str)
        
        self.assertEqual(len(google_recurrence), 1)
        self.assertTrue(google_recurrence[0].startswith("RRULE:"))
        self.assertIn("FREQ=DAILY", google_recurrence[0])
        logger.info(f"Google format: {google_recurrence}")
    
    def test_google_to_rrule_format(self):
        """Test converting Google Calendar format to RRULE."""
        google_recurrence = ["RRULE:FREQ=DAILY;INTERVAL=1;UNTIL=20241231"]
        
        rrule_str = RRuleHandler.google_recurrence_to_rrule(google_recurrence)
        
        self.assertEqual(rrule_str, "FREQ=DAILY;INTERVAL=1;UNTIL=20241231")
        logger.info(f"RRULE format: {rrule_str}")
    
    def test_roundtrip_conversion(self):
        """Test converting app → Google → app."""
        start = datetime(2024, 1, 1)
        original_rrule = RRuleHandler.build_rrule(
            frequency_type="weekly",
            start_date=start,
            frequency_days="0,2,4",
        )
        
        # Convert to Google and back
        google_format = RRuleHandler.rrule_to_google_event_recurrence(original_rrule)
        recovered_rrule = RRuleHandler.google_recurrence_to_rrule(google_format)
        
        # Should be equivalent (may have different spacing)
        self.assertTrue(
            RRuleHandler.compare_rrules(original_rrule, recovered_rrule)
        )
        logger.info(f"Roundtrip: {original_rrule} → {google_format} → {recovered_rrule}")


class RRuleHandlerComparisonTests(TestCase):
    """Test RRULE comparison."""
    
    def test_compare_identical_rrules(self):
        """Test comparing identical RRULEs."""
        rrule_1 = "FREQ=DAILY;INTERVAL=1;UNTIL=20241231"
        rrule_2 = "FREQ=DAILY;INTERVAL=1;UNTIL=20241231"
        
        result = RRuleHandler.compare_rrules(rrule_1, rrule_2)
        self.assertTrue(result)
    
    def test_compare_different_order_rrules(self):
        """Test comparing RRULEs with different component order."""
        rrule_1 = "FREQ=DAILY;INTERVAL=1;UNTIL=20241231"
        rrule_2 = "INTERVAL=1;UNTIL=20241231;FREQ=DAILY"
        
        result = RRuleHandler.compare_rrules(rrule_1, rrule_2)
        self.assertTrue(result)
    
    def test_compare_different_rrules(self):
        """Test comparing different RRULEs."""
        rrule_1 = "FREQ=DAILY;INTERVAL=1"
        rrule_2 = "FREQ=DAILY;INTERVAL=2"
        
        result = RRuleHandler.compare_rrules(rrule_1, rrule_2)
        self.assertFalse(result)


class ConvenienceFunctionTests(TestCase):
    """Test convenience functions."""
    
    def test_build_daily_rrule_convenience(self):
        """Test daily convenience function."""
        rrule = build_daily_rrule(interval=2)
        self.assertIn("FREQ=DAILY", rrule)
        self.assertIn("INTERVAL=2", rrule)
        logger.info(f"Daily convenience: {rrule}")
    
    def test_build_weekly_rrule_convenience(self):
        """Test weekly convenience function."""
        rrule = build_weekly_rrule(weekdays=[0, 2, 4])  # Mon, Wed, Fri
        self.assertIn("FREQ=WEEKLY", rrule)
        self.assertIn("BYDAY=MO,WE,FR", rrule)
        logger.info(f"Weekly convenience: {rrule}")
    
    def test_build_monthly_rrule_convenience(self):
        """Test monthly convenience function."""
        rrule = build_monthly_rrule(day_of_month=15)
        self.assertIn("FREQ=MONTHLY", rrule)
        self.assertIn("BYMONTHDAY=15", rrule)
        logger.info(f"Monthly convenience: {rrule}")
    
    def test_build_yearly_rrule_convenience(self):
        """Test yearly convenience function."""
        rrule = build_yearly_rrule()
        self.assertIn("FREQ=YEARLY", rrule)
        logger.info(f"Yearly convenience: {rrule}")
