"""Human-facing dates; database values remain ISO for sorting and calculations."""

from datetime import date, datetime


def display_date(value, *, with_time=False):
    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        raw = str(value).strip()
        if raw in {"—", "-"}:
            return "—"
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is None:
            try:
                parsed = datetime.strptime(raw, "%d.%m.%Y")
            except ValueError:
                return raw
    result = parsed.strftime("%d.%m.%Y")
    if with_time and parsed.time() != datetime.min.time():
        result += parsed.strftime(" %H:%M")
    return result


def display_row_dates(row, *fields):
    """Prepare selected database date fields for a UI table."""
    result = dict(row)
    for field in fields:
        if field in result:
            result[field] = display_date(result[field], with_time=field.endswith("_at"))
    return result
