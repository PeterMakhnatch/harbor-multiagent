from datetime import UTC, datetime


def known_utc_timestamp(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise TypeError("Expected a datetime or an ISO timestamp string.")
    if value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def require_utc_timestamp(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime.")
    timestamp = known_utc_timestamp(value)
    if timestamp is None:
        raise ValueError(f"{field_name} must include a UTC offset.")
    return timestamp
