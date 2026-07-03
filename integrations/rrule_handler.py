"""
RRULE (Recurrence Rule) handler for converting between app frequency formats and Google Calendar RRULE format.

Supports bidirectional conversion:
- App format: {frequency_type, frequency_interval, frequency_days, start_date, end_date, day_of_week, day_of_month}
- Google format: RFC 5545 RRULE (FREQ=DAILY;INTERVAL=1;UNTIL=20260630;etc.)
"""

import logging
from datetime import datetime, timedelta, time
from typing import Optional, Dict, List, Any, Tuple
from dateutil.rrule import rrule, DAILY, WEEKLY, MONTHLY, YEARLY, HOURLY, MO, TU, WE, TH, FR, SA, SU
from dateutil.rrule import rrulestr

logger = logging.getLogger(__name__)


def _normalize_until_for_expansion(rrule_str: str) -> str:
    parts = []
    for part in rrule_str.split(";"):
        if part.startswith("UNTIL="):
            value = part.split("=", 1)[1]
            if len(value) == 8:
                part = f"UNTIL={value}T235959"
        parts.append(part)
    return ";".join(parts)

# Mapping app frequency types to dateutil.rrule constants
FREQ_MAP = {
    "daily": DAILY,
    "hourly": HOURLY,
    "weekly": WEEKLY,
    "monthly": MONTHLY,
    "yearly": YEARLY,
}

# Mapping weekday numbers (0-6) to dateutil.rrule constants
WEEKDAY_MAP = {
    0: MO,  # Monday
    1: TU,  # Tuesday
    2: WE,  # Wednesday
    3: TH,  # Thursday
    4: FR,  # Friday
    5: SA,  # Saturday
    6: SU,  # Sunday
}


class RRuleHandler:
    """
    Handles conversion between app task frequency and Google Calendar RRULE format.
    
    Example usage:
        handler = RRuleHandler()
        rrule_str = handler.build_rrule(
            frequency_type="daily",
            frequency_interval=1,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 12, 31)
        )
        # Returns: "FREQ=DAILY;INTERVAL=1;UNTIL=20241231"
    """
    
    @staticmethod
    def build_rrule(
        frequency_type: str,
        start_date: datetime,
        frequency_interval: int = 1,
        end_date: Optional[datetime] = None,
        frequency_days: Optional[str] = None,
        day_of_week: Optional[int] = None,
        day_of_month: Optional[int] = None,
    ) -> str:
        """
        Convert app frequency parameters to RRULE string format.
        
        Args:
            frequency_type: One of 'daily', 'weekly', 'monthly', 'yearly'
            start_date: Task start date
            frequency_interval: How many units between occurrences (default 1)
            end_date: When recurrence ends (optional)
            frequency_days: Comma-separated weekday numbers for weekly (0=Mon, 6=Sun)
            day_of_week: Single weekday number (0=Mon, 6=Sun)
            day_of_month: Day of month for monthly/yearly (1-31)
        
        Returns:
            RRULE string like "FREQ=DAILY;INTERVAL=1;UNTIL=20260630"
        
        Raises:
            ValueError: If frequency_type or other parameters are invalid
        """
        if frequency_type not in FREQ_MAP:
            raise ValueError(
                f"Invalid frequency_type: {frequency_type}. "
                f"Must be one of {list(FREQ_MAP.keys())}"
            )
        
        freq = FREQ_MAP[frequency_type]
        rrule_parts = [f"FREQ={frequency_type.upper()}"]
        
        # Add interval
        if frequency_interval:
            rrule_parts.append(f"INTERVAL={frequency_interval}")
        
        # Add UNTIL date (end_date)
        if end_date:
            end_date = end_date - timedelta(hours=5, minutes=30)
            until_str = end_date.strftime("%Y%m%dT%H%M%SZ")
            rrule_parts.append(f"UNTIL={until_str}")
        
        # Add BYDAY for weekly or specific weekdays
        if frequency_type == "weekly":
            if frequency_days:
                if isinstance(frequency_days, (list, tuple, set)):
                    days_list = [int(d) for d in frequency_days if str(d).strip() != ""]
                else:
                    days_list = [int(d.strip()) for d in str(frequency_days).split(",") if d.strip() != ""]
                weekday_abbrev = {0: "MO", 1: "TU", 2: "WE", 3: "TH", 4: "FR", 5: "SA", 6: "SU"}
                weekday_strs = [weekday_abbrev[d] for d in days_list if d in weekday_abbrev]
                if weekday_strs:
                    rrule_parts.append(f"BYDAY={','.join(weekday_strs)}")
            elif day_of_week is not None:
                weekday_abbrev = {0: "MO", 1: "TU", 2: "WE", 3: "TH", 4: "FR", 5: "SA", 6: "SU"}
                weekday_str = weekday_abbrev[int(day_of_week)]
                rrule_parts.append(f"BYDAY={weekday_str}")
        
        # Add BYMONTHDAY for monthly recurrence
        if frequency_type == "monthly" and day_of_month:
            if isinstance(day_of_month, (list, tuple, set)):
                monthdays = []
                for item in day_of_month:
                    try:
                        day_value = int(item)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= day_value <= 31:
                        monthdays.append(str(day_value))
                if monthdays:
                    rrule_parts.append(f"BYMONTHDAY={','.join(sorted(set(monthdays), key=int))}")
            else:
                try:
                    day_value = int(day_of_month)
                except (TypeError, ValueError):
                    day_value = None
                if day_value and 1 <= day_value <= 31:
                    rrule_parts.append(f"BYMONTHDAY={day_value}")
        
        rrule_str = ";".join(rrule_parts)
        logger.info(f"Built RRULE: {rrule_str}")
        print(f"Built RRULE: {rrule_str}")
        return rrule_str


def build_recurrence_rule_for_entity(entity) -> Optional[str]:
    frequency_type = getattr(entity, "frequency_type", None)
    if not frequency_type or frequency_type == "once":
        return None

    start_date = getattr(entity, "start_date", None)
    start_time = getattr(entity, "start_time", None)
    if not start_date or not start_time:
        return None

    start_dt = datetime.combine(start_date, start_time)
    end_date = getattr(entity, "end_date", None)
    end_time = getattr(entity, "end_time", None) or time(23, 59, 59)  # Default to end of day if not specified
    if end_date:
        # Check if the cutoff time is earlier than the event start time on that day
        if datetime.combine(end_date, end_time) < datetime.combine(end_date, start_time):
            end_date = end_date - timedelta(days=1)
        end_dt = datetime.combine(end_date, time(23, 59, 59))  # Use end of day for UNTIL
    else:
        end_dt = None

    try:
        interval = max(int(getattr(entity, "frequency_interval", 1) or 1), 1)
    except (TypeError, ValueError):
        interval = 1

    frequency_days = getattr(entity, "frequency_days", None) or []
    day_of_week = getattr(entity, "day_of_week", None)
    day_of_month = getattr(entity, "day_of_month", None)
    if not day_of_month and frequency_days:
        monthdays = []
        for item in frequency_days:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= value <= 31:
                monthdays.append(value)
        if monthdays:
            day_of_month = monthdays[0] if len(monthdays) == 1 else monthdays
    print(f"Building RRULE for entity {entity}: frequency_type={frequency_type}, start_dt={start_dt}, end_dt={end_dt}, interval={interval}, frequency_days={frequency_days}, day_of_week={day_of_week}, day_of_month={day_of_month}")
    if frequency_type == "daily":
        return RRuleHandler.build_rrule(
            frequency_type="daily",
            start_date=start_dt,
            frequency_interval=interval,
            end_date=end_dt,
        )

    if frequency_type == "hourly":
        return RRuleHandler.build_rrule(
            frequency_type="hourly",
            start_date=start_dt,
            frequency_interval=interval,
            end_date=end_dt,
        )

    if frequency_type == "weekly":
        if frequency_days:
            return RRuleHandler.build_rrule(
                frequency_type="weekly",
                start_date=start_dt,
                frequency_interval=interval,
                end_date=end_dt,
                frequency_days=frequency_days,
            )
        return RRuleHandler.build_rrule(
            frequency_type="weekly",
            start_date=start_dt,
            frequency_interval=interval,
            end_date=end_dt,
            day_of_week=day_of_week if day_of_week is not None else start_date.weekday(),
        )

    if frequency_type == "monthly":
        if day_of_month is None:
            day_of_month = start_date.day
        return RRuleHandler.build_rrule(
            frequency_type="monthly",
            start_date=start_dt,
            frequency_interval=interval,
            end_date=end_dt,
            day_of_month=day_of_month,
        )

    if frequency_type == "yearly":
        return RRuleHandler.build_rrule(
            frequency_type="yearly",
            start_date=start_dt,
            frequency_interval=interval,
            end_date=end_dt,
        )

    if frequency_type == "custom":
        weekday_days = []
        month_days = []
        for value in frequency_days:
            try:
                day_value = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= day_value <= 6:
                weekday_days.append(day_value)
            elif 1 <= day_value <= 31:
                month_days.append(day_value)

        if weekday_days:
            return RRuleHandler.build_rrule(
                frequency_type="weekly",
                start_date=start_dt,
                frequency_interval=interval,
                end_date=end_dt,
                frequency_days=weekday_days,
            )

        if month_days:
            return RRuleHandler.build_rrule(
                frequency_type="monthly",
                start_date=start_dt,
                frequency_interval=interval,
                end_date=end_dt,
                day_of_month=month_days if len(month_days) > 1 else month_days[0],
            )

        period = getattr(entity, "frequency_period", None)
        if period == "day":
            return RRuleHandler.build_rrule(
                frequency_type="daily",
                start_date=start_dt,
                frequency_interval=interval,
                end_date=end_dt,
            )
        if period == "week":
            return RRuleHandler.build_rrule(
                frequency_type="weekly",
                start_date=start_dt,
                frequency_interval=interval,
                end_date=end_dt,
                day_of_week=start_date.weekday(),
            )
        if period == "month":
            return RRuleHandler.build_rrule(
                frequency_type="monthly",
                start_date=start_dt,
                frequency_interval=interval,
                end_date=end_dt,
                day_of_month=start_date.day,
            )

    return None
    
    @staticmethod
    def parse_rrule(rrule_str: str) -> Dict[str, Any]:
        """
        Parse RRULE string into component parts.
        
        Args:
            rrule_str: RRULE string like "FREQ=DAILY;INTERVAL=2;UNTIL=20260630"
        
        Returns:
            Dict with keys: frequency_type, interval, until_date, byday, bymonthday, etc.
        
        Raises:
            ValueError: If RRULE format is invalid
        """
        parsed = {}
        
        try:
            parts = rrule_str.split(";")
            for part in parts:
                if "=" not in part:
                    continue
                
                key, value = part.split("=", 1)
                key = key.strip()
                value = value.strip()
                
                if key == "FREQ":
                    parsed["frequency_type"] = value.lower()
                elif key == "INTERVAL":
                    parsed["frequency_interval"] = int(value)
                elif key == "UNTIL":
                    # Parse UNTIL date (YYYYMMDD format)
                    parsed["until_date"] = datetime.strptime(value, "%Y%m%d")
                elif key == "BYDAY":
                    # Convert "MO,WE,FR" to [0, 2, 4]
                    day_codes = value.split(",")
                    parsed["byday"] = day_codes
                elif key == "BYMONTHDAY":
                    parsed["bymonthday"] = int(value)
                elif key == "BYYEARDAY":
                    parsed["byyearday"] = int(value)
                elif key == "BYWEEKNO":
                    parsed["byweekno"] = int(value)
                else:
                    # Store any other RRULE components
                    parsed[key.lower()] = value
            
            logger.info(f"Parsed RRULE: {parsed}")
            return parsed
        except Exception as e:
            logger.error(f"Error parsing RRULE '{rrule_str}': {str(e)}")
            raise ValueError(f"Invalid RRULE format: {rrule_str}") from e
    
    @staticmethod
    def expand_rrule(
        rrule_str: str,
        start_date: datetime,
        end_date: Optional[datetime] = None,
        count: int = 10,
    ) -> List[datetime]:
        """
        Expand RRULE into individual occurrence dates.
        
        Args:
            rrule_str: RRULE string to expand
            start_date: First occurrence date/time
            end_date: Optional override for UNTIL date (default 2 years in future for yearly, 1 year for others)
            count: Maximum number of occurrences to generate (default 10)
        
        Returns:
            List of datetime objects representing each occurrence
        
        Example:
            dates = handler.expand_rrule(
                "FREQ=WEEKLY;BYDAY=MO,WE,FR",
                start_date=datetime(2024, 1, 1),
                count=5
            )
            # Returns 5 dates on Mon/Wed/Fri starting from 2024-01-01
        """
        try:
            # Parse the RRULE and get all components
            normalized_rrule_str = _normalize_until_for_expansion(rrule_str)
            full_rrule_str = normalized_rrule_str
            
            # If start_date is not in the RRULE, prepend it
            if "DTSTART" not in full_rrule_str:
                # The rrulestr function needs DTSTART
                full_rrule_str = f"DTSTART:{start_date.strftime('%Y%m%dT%H%M%S')}\nRRULE:{normalized_rrule_str}"
            
            # Use dateutil.rrule to generate occurrences
            rule = rrulestr(full_rrule_str, dtstart=start_date)
            
            # Determine default end_date based on RRULE frequency
            if end_date is None:
                # Default to a wide range to catch yearly/monthly/weekly recurring
                if "FREQ=YEARLY" in normalized_rrule_str:
                    end_date = start_date + timedelta(days=3*365)  # 3 years
                else:
                    end_date = start_date + timedelta(days=365)  # 1 year
            
            # Generate occurrences
            occurrences = list(rule.between(
                start_date,
                end_date,
                inc=True
            ))
            
            # Limit to count
            occurrences = occurrences[:count]
            
            logger.info(f"Expanded RRULE to {len(occurrences)} occurrences")
            return occurrences
        except Exception as e:
            logger.error(f"Error expanding RRULE '{rrule_str}': {str(e)}")
            raise ValueError(f"Cannot expand RRULE: {rrule_str}") from e
    
    @staticmethod
    def get_next_occurrence(
        rrule_str: str,
        start_date: datetime,
        after_date: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """
        Get the next occurrence after a given date.
        
        Args:
            rrule_str: RRULE string
            start_date: Initial start date
            after_date: Return first occurrence after this date (default now)
        
        Returns:
            Next datetime, or None if no more occurrences
        """
        after_date = after_date or datetime.now()
        try:
            full_rrule_str = f"DTSTART:{start_date.strftime('%Y%m%dT%H%M%S')}\n{rrule_str}"
            rule = rrulestr(full_rrule_str, dtstart=start_date)
            
            next_occ = rule.after(after_date, inc=False)
            if next_occ:
                logger.info(f"Next occurrence after {after_date}: {next_occ}")
            return next_occ
        except Exception as e:
            logger.error(f"Error getting next occurrence: {str(e)}")
            return None
    
    @staticmethod
    def rrule_to_google_event_recurrence(rrule_str: str) -> List[str]:
        """
        Convert RRULE string to Google Calendar's recurrence format.
        Google Calendar expects a list of RRULE strings in the 'recurrence' field.
        
        Args:
            rrule_str: Single RRULE string
        
        Returns:
            List containing the RRULE string (Google expects list format)
        
        Example:
            google_recurrence = handler.rrule_to_google_event_recurrence(
                "FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20261231"
            )
            # Returns ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20261231"]
        """
        if not rrule_str:
            return []
        
        # Google format needs "RRULE:" prefix
        google_rrule = f"RRULE:{rrule_str}"
        logger.info(f"Converted to Google recurrence format: {google_rrule}")
        return [google_rrule]
    
    @staticmethod
    def google_recurrence_to_rrule(google_recurrence: List[str]) -> Optional[str]:
        """
        Convert Google Calendar's recurrence format to RRULE string.
        
        Args:
            google_recurrence: List of recurrence strings like ["RRULE:FREQ=DAILY;..."]
        
        Returns:
            RRULE string without "RRULE:" prefix, or None
        """
        if not google_recurrence or len(google_recurrence) == 0:
            return None
        
        # Take first recurrence rule
        rule_str = google_recurrence[0]
        
        # Remove "RRULE:" prefix if present
        if rule_str.startswith("RRULE:"):
            rrule_str = rule_str[6:]  # Remove "RRULE:" prefix
        else:
            rrule_str = rule_str
        
        logger.info(f"Converted from Google recurrence: {rrule_str}")
        return rrule_str
    
    @staticmethod
    def compare_rrules(rrule_1: str, rrule_2: str) -> bool:
        """
        Compare two RRULE strings for equivalence (order-independent).
        
        Args:
            rrule_1: First RRULE string
            rrule_2: Second RRULE string
        
        Returns:
            True if RRULEs are equivalent
        """
        try:
            # Parse both into sorted component lists
            parts_1 = sorted(rrule_1.split(";"))
            parts_2 = sorted(rrule_2.split(";"))
            
            result = parts_1 == parts_2
            if result:
                logger.info(f"RRULEs are equivalent")
            else:
                logger.info(f"RRULEs differ: {rrule_1} vs {rrule_2}")
            return result
        except Exception as e:
            logger.error(f"Error comparing RRULEs: {str(e)}")
            return False


# Convenience functions for common operations
def build_daily_rrule(
    interval: int = 1,
    end_date: Optional[datetime] = None,
) -> str:
    """Build a daily RRULE. Example: FREQ=DAILY;INTERVAL=2;UNTIL=20260630"""
    start = datetime.now()
    return RRuleHandler.build_rrule(
        frequency_type="daily",
        start_date=start,
        frequency_interval=interval,
        end_date=end_date,
    )


def build_weekly_rrule(
    weekdays: List[int],
    interval: int = 1,
    end_date: Optional[datetime] = None,
) -> str:
    """Build a weekly RRULE for specific weekdays. Weekdays: 0=Mon, 6=Sun"""
    start = datetime.now()
    frequency_days = ",".join(str(d) for d in weekdays)
    return RRuleHandler.build_rrule(
        frequency_type="weekly",
        start_date=start,
        frequency_interval=interval,
        frequency_days=frequency_days,
        end_date=end_date,
    )


def build_monthly_rrule(
    day_of_month: int,
    interval: int = 1,
    end_date: Optional[datetime] = None,
) -> str:
    """Build a monthly RRULE. Example: FREQ=MONTHLY;BYMONTHDAY=15;UNTIL=20260630"""
    start = datetime.now()
    return RRuleHandler.build_rrule(
        frequency_type="monthly",
        start_date=start,
        frequency_interval=interval,
        day_of_month=day_of_month,
        end_date=end_date,
    )


def build_yearly_rrule(
    end_date: Optional[datetime] = None,
) -> str:
    """Build a yearly RRULE. Example: FREQ=YEARLY;UNTIL=20260630"""
    start = datetime.now()
    return RRuleHandler.build_rrule(
        frequency_type="yearly",
        start_date=start,
        end_date=end_date,
    )


def parse_rrule(rrule_str: str) -> Dict[str, Any]:
    parsed = {}

    try:
        parts = rrule_str.split(";")
        for part in parts:
            if "=" not in part:
                continue

            key, value = part.split("=", 1)
            key = key.strip()
            value = value.strip()

            if key == "FREQ":
                parsed["frequency_type"] = value.lower()
            elif key == "INTERVAL":
                parsed["frequency_interval"] = int(value)
            elif key == "UNTIL":
                parsed["until_date"] = datetime.strptime(value, "%Y%m%d")
            elif key == "BYDAY":
                parsed["byday"] = value.split(",")
            elif key == "BYMONTHDAY":
                parsed["bymonthday"] = int(value)
            elif key == "BYYEARDAY":
                parsed["byyearday"] = int(value)
            elif key == "BYWEEKNO":
                parsed["byweekno"] = int(value)
            else:
                parsed[key.lower()] = value

        logger.info(f"Parsed RRULE: {parsed}")
        return parsed
    except Exception as e:
        logger.error(f"Error parsing RRULE '{rrule_str}': {str(e)}")
        raise ValueError(f"Invalid RRULE format: {rrule_str}") from e


def expand_rrule(
    rrule_str: str,
    start_date: datetime,
    end_date: Optional[datetime] = None,
    count: int = 10,
) -> List[datetime]:
    try:
        normalized_rrule_str = _normalize_until_for_expansion(rrule_str)
        full_rrule_str = normalized_rrule_str

        if "DTSTART" not in full_rrule_str:
            full_rrule_str = f"DTSTART:{start_date.strftime('%Y%m%dT%H%M%S')}\nRRULE:{normalized_rrule_str}"

        rule = rrulestr(full_rrule_str, dtstart=start_date)

        if end_date is None:
            if "FREQ=YEARLY" in normalized_rrule_str:
                end_date = start_date + timedelta(days=3 * 365)
            else:
                end_date = start_date + timedelta(days=365)

        occurrences = list(rule.between(start_date, end_date, inc=True))
        occurrences = occurrences[:count]
        logger.info(f"Expanded RRULE to {len(occurrences)} occurrences")
        return occurrences
    except Exception as e:
        logger.error(f"Error expanding RRULE '{rrule_str}': {str(e)}")
        raise ValueError(f"Cannot expand RRULE: {rrule_str}") from e


def get_next_occurrence(
    rrule_str: str,
    start_date: datetime,
    after_date: Optional[datetime] = None,
) -> Optional[datetime]:
    after_date = after_date or datetime.now()
    try:
        full_rrule_str = f"DTSTART:{start_date.strftime('%Y%m%dT%H%M%S')}\n{rrule_str}"
        rule = rrulestr(full_rrule_str, dtstart=start_date)

        next_occ = rule.after(after_date, inc=False)
        if next_occ:
            logger.info(f"Next occurrence after {after_date}: {next_occ}")
        return next_occ
    except Exception as e:
        logger.error(f"Error getting next occurrence: {str(e)}")
        return None


def rrule_to_google_event_recurrence(rrule_str: str) -> List[str]:
    if not rrule_str:
        return []

    google_rrule = f"RRULE:{rrule_str}"
    logger.info(f"Converted to Google recurrence format: {google_rrule}")
    return [google_rrule]


def google_recurrence_to_rrule(google_recurrence: List[str]) -> Optional[str]:
    if not google_recurrence:
        return None

    rule_str = google_recurrence[0]
    if rule_str.startswith("RRULE:"):
        rrule_str = rule_str[6:]
    else:
        rrule_str = rule_str

    logger.info(f"Converted from Google recurrence: {rrule_str}")
    return rrule_str


def compare_rrules(rrule_1: str, rrule_2: str) -> bool:
    try:
        parts_1 = sorted(rrule_1.split(";"))
        parts_2 = sorted(rrule_2.split(";"))
        result = parts_1 == parts_2
        if result:
            logger.info("RRULEs are equivalent")
        else:
            logger.info(f"RRULEs differ: {rrule_1} vs {rrule_2}")
        return result
    except Exception as e:
        logger.error(f"Error comparing RRULEs: {str(e)}")
        return False


RRuleHandler.parse_rrule = staticmethod(parse_rrule)
RRuleHandler.expand_rrule = staticmethod(expand_rrule)
RRuleHandler.get_next_occurrence = staticmethod(get_next_occurrence)
RRuleHandler.rrule_to_google_event_recurrence = staticmethod(rrule_to_google_event_recurrence)
RRuleHandler.google_recurrence_to_rrule = staticmethod(google_recurrence_to_rrule)
RRuleHandler.compare_rrules = staticmethod(compare_rrules)
