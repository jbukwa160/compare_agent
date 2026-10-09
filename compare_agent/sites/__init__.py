"""Site adapters. Importing `load_adapters` registers every adapter type."""
from . import comparison, shopping  # noqa: F401
from . import generic as load_adapters  # noqa: F401
