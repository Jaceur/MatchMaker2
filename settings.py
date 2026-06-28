"""Runtime-tunable settings, stored in the database so the web app and the local
runners all read the same value.

Today this holds the lead-qualification bar set from the admin dashboard slider.
The slider is a friendly 0-100% that maps onto a 30-50 lead_score band, so:

    0%  -> bar 30   (let most real companies through)
    50% -> bar 40   (the default)
    100%-> bar 50   (only the strongest)

(Top of the band is 50, not 70 — a 70 fit score is very hard to reach in practice.)
"""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import engine
from models import app_settings

QUALIFY_BAR_MIN = 30
QUALIFY_BAR_MAX = 50
DEFAULT_QUALIFY_PERCENT = 50


def get_setting(key, default=None):
    """Read one setting's value (a string), or `default` if it isn't set yet."""
    with engine.connect() as conn:
        row = conn.execute(
            select(app_settings.c.value).where(app_settings.c.key == key)
        ).first()
    return row[0] if row else default


def set_setting(key, value):
    """Insert or update a setting (Postgres upsert)."""
    stmt = pg_insert(app_settings).values(key=key, value=str(value))
    stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": str(value)})
    with engine.begin() as conn:
        conn.execute(stmt)


def qualify_percent_to_bar(percent):
    """Map the 0-100% slider onto the 30-50 score band (0% -> 30, 100% -> 50)."""
    span = QUALIFY_BAR_MAX - QUALIFY_BAR_MIN            # 20
    return round(QUALIFY_BAR_MIN + (percent / 100) * span)


def get_qualify_percent():
    """The slider value, 0-100 (defaults to 50% until an admin changes it)."""
    raw = get_setting("qualify_percent", DEFAULT_QUALIFY_PERCENT)
    try:
        return max(0, min(100, int(float(raw))))
    except (TypeError, ValueError):
        return DEFAULT_QUALIFY_PERCENT


def set_qualify_percent(percent):
    set_setting("qualify_percent", int(percent))


def get_qualify_bar():
    """The current minimum lead_score (30-50) a lead must reach to qualify. This
    is what the staged pipeline gates on."""
    return qualify_percent_to_bar(get_qualify_percent())
