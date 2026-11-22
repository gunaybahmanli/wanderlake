"""The gold tables published to Supabase (schema api) and R2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyiceberg.expressions import BooleanExpression

EXCHANGE_RATE_DAYS = 400  # fct_exchange_rate: only the last 400 days are published


@dataclass(frozen=True)
class PublishedTable:
    name: str  # same name in gold (Iceberg) and api (Supabase)
    days_column: str | None = None  # date column for a "last N days" filter
    days: int | None = None

    @property
    def source(self) -> str:
        return f"gold.{self.name}"

    def cutoff(self, snapshot_date: date) -> date | None:
        """First date kept, or None when the whole table is published."""
        if self.days_column is None or self.days is None:
            return None
        return snapshot_date - timedelta(days=self.days)

    def row_filter(self, snapshot_date: date) -> BooleanExpression:
        # Imported here: the publish_cloud DAG imports this module while it is parsed.
        from pyiceberg.expressions import AlwaysTrue, GreaterThanOrEqual

        cutoff = self.cutoff(snapshot_date)
        if cutoff is None:
            return AlwaysTrue()
        return GreaterThanOrEqual(self.days_column, cutoff.isoformat())


TABLES: tuple[PublishedTable, ...] = (
    PublishedTable("dim_country"),
    PublishedTable("dim_city"),
    PublishedTable("city_overview"),
    PublishedTable("fct_exchange_rate", days_column="date", days=EXCHANGE_RATE_DAYS),
    PublishedTable("fct_weather_daily"),
    PublishedTable("agg_city_climate_monthly"),
    PublishedTable("weather_forecast_latest"),
    PublishedTable("travel_advice_current"),
)
TABLE_NAMES: tuple[str, ...] = tuple(t.name for t in TABLES)


def get_table(name: str) -> PublishedTable:
    for table in TABLES:
        if table.name == name:
            return table
    raise KeyError(f"not a published table: {name!r} (published: {', '.join(TABLE_NAMES)})")
