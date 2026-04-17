from dataclasses import dataclass, field
from datetime import datetime
import json
import os
import re

import yaml


@dataclass
class TripEvent:
    title: str
    start: str  # ISO format: 2026-06-15T09:00
    end: str
    location: str = ""
    description: str = ""
    category: str = "activity"  # flight, hotel, meal, activity, transit, reminder
    reminder_minutes: int = 30


@dataclass
class DayPlan:
    date: str  # 2026-06-15
    events: list[TripEvent] = field(default_factory=list)


@dataclass
class Itinerary:
    title: str
    timezone: str
    days: list[DayPlan] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "timezone": self.timezone,
            "days": [
                {
                    "date": d.date,
                    "events": [
                        {
                            "title": e.title,
                            "start": e.start,
                            "end": e.end,
                            "location": e.location,
                            "description": e.description,
                            "category": e.category,
                            "reminder_minutes": e.reminder_minutes,
                        }
                        for e in d.events
                    ],
                }
                for d in self.days
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Itinerary":
        days = []
        for d in data.get("days", []):
            events = [
                TripEvent(
                    title=e["title"],
                    start=e["start"],
                    end=e["end"],
                    location=e.get("location", ""),
                    description=e.get("description", ""),
                    category=e.get("category", "activity"),
                    reminder_minutes=e.get("reminder_minutes", 30),
                )
                for e in d.get("events", [])
            ]
            days.append(DayPlan(date=d["date"], events=events))
        return cls(
            title=data["title"],
            timezone=data["timezone"],
            days=days,
        )


@dataclass
class Config:
    bot_token: str
    chat_id: str
    anthropic_api_key: str
    anthropic_model: str
    tavily_api_key: str
    server_host: str
    server_port: int
    server_base_url: str
    data_dir: str

    @classmethod
    def from_yaml(cls, data: dict) -> "Config":
        def resolve_env(val: str) -> str:
            if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
                env_var = val[2:-1]
                return os.environ.get(env_var, "")
            return str(val)

        tg = data.get("telegram", {})
        ant = data.get("anthropic", {})
        tav = data.get("tavily", {})
        srv = data.get("server", {})

        return cls(
            bot_token=resolve_env(tg.get("bot_token", "")),
            chat_id=resolve_env(tg.get("chat_id", "")),
            anthropic_api_key=resolve_env(ant.get("api_key", "")),
            anthropic_model=ant.get("model", "claude-sonnet-4-6"),
            tavily_api_key=resolve_env(tav.get("api_key", "")),
            server_host=srv.get("host", "0.0.0.0"),
            server_port=int(srv.get("port", 8099)),
            server_base_url=srv.get("base_url", "http://localhost:8099"),
            data_dir=data.get("data_dir", "./data"),
        )
