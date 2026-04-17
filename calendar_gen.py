import logging
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

from icalendar import Calendar, Event, Alarm
import pytz

from models import Itinerary

logger = logging.getLogger(__name__)

CATEGORY_EMOJI = {
    "flight": "✈️",
    "hotel": "🏨",
    "meal": "🍽️",
    "activity": "🏛️",
    "transit": "🚆",
    "reminder": "💊",
}


def generate_ics(itinerary: Itinerary, trip_id: str) -> bytes:
    """Generate an ICS calendar from a structured itinerary."""
    cal = Calendar()
    cal.add("prodid", "-//XiaoLan Travel Bot//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", itinerary.title)
    cal.add("x-wr-timezone", itinerary.timezone)

    tz = pytz.timezone(itinerary.timezone)

    for day in itinerary.days:
        for evt in day.events:
            event = Event()

            # Stable UID based on trip_id + event details so updates replace correctly
            uid_seed = f"{trip_id}/{evt.start}/{evt.title}"
            uid = str(uuid5(NAMESPACE_URL, uid_seed))
            event.add("uid", uid)

            emoji = CATEGORY_EMOJI.get(evt.category, "")
            title = f"{emoji} {evt.title}" if emoji else evt.title
            event.add("summary", title)

            # Parse times
            start_dt = tz.localize(datetime.fromisoformat(evt.start))
            end_dt = tz.localize(datetime.fromisoformat(evt.end))
            event.add("dtstart", start_dt)
            event.add("dtend", end_dt)

            if evt.location:
                event.add("location", evt.location)

            if evt.description:
                event.add("description", evt.description)

            # Add category
            event.add("categories", [evt.category])

            # Add alarm/reminder
            if evt.reminder_minutes > 0:
                alarm = Alarm()
                alarm.add("action", "DISPLAY")
                alarm.add("description", f"Reminder: {evt.title}")
                alarm.add("trigger", timedelta(minutes=-evt.reminder_minutes))
                event.add_component(alarm)

            cal.add_component(event)

    return cal.to_ical()


def save_ics(ics_bytes: bytes, data_dir: str, trip_id: str) -> Path:
    """Save .ics file to disk and return the path."""
    trip_dir = Path(data_dir) / "trips" / trip_id
    trip_dir.mkdir(parents=True, exist_ok=True)
    ics_path = trip_dir / "calendar.ics"
    ics_path.write_bytes(ics_bytes)
    logger.info(f"Saved calendar to {ics_path}")
    return ics_path
