"""Part 1.2 and 1.3 of the brief: everything computed from the cleaned tables."""
from .enrich import caffeine_source, enrich
from .filters import apply_filters, sort_rows
from .stats import calorie_bands, category_means, compare, describe, intake, macro_energy, top_items
from .tables import metrics_in, records
