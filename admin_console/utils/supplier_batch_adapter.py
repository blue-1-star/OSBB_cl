"""Resolve the current local batch API in long-lived Streamlit processes."""
import inspect
from importlib import reload
import service_preorders_core


def create_local_supplier_batch(**kwargs):
    # Reload only an old API, not on every rerun or during an active transaction.
    if 'local_single_user' not in inspect.signature(service_preorders_core.create_supplier_batch).parameters:
        reload(service_preorders_core)
    return service_preorders_core.create_supplier_batch(local_single_user=True, **kwargs)
