"""Registry of product pipelines. Add a product by adding a module with a PIPELINE."""
from . import cleanup, xray

PIPELINES = {p.product: p for p in (xray.PIPELINE, cleanup.PIPELINE)}
