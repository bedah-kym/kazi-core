"""
Connector auto-discovery and registration.

Scans the connectors directory for BaseConnector subclasses and builds
a unified action-to-connector map. Also loads legacy hardcoded connectors
for backward compatibility.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Singleton caches
_discovered_connectors: Optional[Dict[str, Any]] = None
_registered_catalog_entries: List[Dict[str, Any]] = []


def discover_connectors() -> Dict[str, Any]:
    """
    Build the action-to-connector map by:
    1. Loading legacy hardcoded connectors (backward compat)
    2. Scanning connectors/ directory for new-style BaseConnector subclasses
    3. Scanning installed packages for 'kazi.connectors' entry points

    New-style connectors override legacy ones if there's a conflict.
    """
    global _discovered_connectors, _registered_catalog_entries

    if _discovered_connectors is not None:
        return _discovered_connectors

    connector_map: Dict[str, Any] = {}

    # Step 1: Load legacy hardcoded connectors
    legacy = _load_legacy_connectors()
    connector_map.update(legacy)
    logger.info("Loaded %d legacy connector mappings", len(legacy))

    # Step 2: Scan connectors/ directory for new-style connectors
    new_style = _scan_connectors_directory()
    for connector in new_style:
        ok, msg = connector.validate_config()
        if not ok:
            logger.warning("Connector %s skipped: %s", connector.name, msg)
            continue

        # Register actions
        for action_name in connector.actions:
            connector_map[action_name] = connector

        # Collect catalog entries
        try:
            entries = connector.get_action_catalog_entries()
            _registered_catalog_entries.extend(entries)
        except Exception as exc:
            logger.warning(
                "Connector %s: get_action_catalog_entries() failed: %s",
                connector.name, exc,
            )

        logger.info("Registered connector: %s v%s (%d actions)",
                     connector.name, connector.version, len(connector.actions))

    # Step 3: Scan entry points (for pip-installed community connectors)
    entrypoint_connectors = _scan_entry_points()
    for connector in entrypoint_connectors:
        ok, msg = connector.validate_config()
        if not ok:
            logger.warning("Entry-point connector %s skipped: %s", connector.name, msg)
            continue
        for action_name in connector.actions:
            connector_map[action_name] = connector
        try:
            entries = connector.get_action_catalog_entries()
            _registered_catalog_entries.extend(entries)
        except Exception as exc:
            logger.warning(
                "Entry-point connector %s: get_action_catalog_entries() failed: %s",
                connector.name, exc,
            )
        logger.info("Registered entry-point connector: %s v%s",
                     connector.name, connector.version)

    # Step 4: Register all discovered catalog entries into the action catalog
    if _registered_catalog_entries:
        from orchestration.action_catalog import register_actions
        register_actions(_registered_catalog_entries)
        logger.info("Registered %d catalog entries from discovered connectors",
                     len(_registered_catalog_entries))

    _discovered_connectors = connector_map
    logger.info("Total connector map: %d action mappings", len(connector_map))
    return connector_map


def get_registered_catalog_entries() -> List[Dict[str, Any]]:
    """Return catalog entries from all discovered new-style connectors."""
    return list(_registered_catalog_entries)


def reset_registry() -> None:
    """Clear the registry (useful for testing)."""
    global _discovered_connectors, _registered_catalog_entries
    _discovered_connectors = None
    _registered_catalog_entries = []


def _load_legacy_connectors() -> Dict[str, Any]:
    """Load the existing hardcoded connector map for backward compatibility."""
    try:
        from orchestration.connectors.whatsapp_connector import WhatsAppConnector
        from orchestration.connectors.intersend_connector import IntersendPayConnector
        from orchestration.connectors.gmail_connector import GmailConnector
        from orchestration.connectors.quota_connector import QuotaConnector
        from orchestration.connectors.payment_connector import ReadOnlyPaymentConnector
        from orchestration.connectors.invoice_connector import InvoiceConnector
        from orchestration.connectors.travel_buses_connector import TravelBusesConnector
        from orchestration.connectors.travel_hotels_connector import TravelHotelsConnector
        from orchestration.connectors.travel_flights_connector import TravelFlightsConnector
        from orchestration.connectors.travel_transfers_connector import TravelTransfersConnector
        from orchestration.connectors.travel_events_connector import TravelEventsConnector
        from orchestration.connectors.itinerary_connector import ItineraryConnector
        from orchestration.mcp_router import (
            CalendarConnector,
            SearchConnector,
            WeatherConnector,
            GiphyConnector,
            CurrencyConnector,
            ReminderConnector,
        )

        return {
            "schedule_meeting": CalendarConnector(),
            "check_availability": CalendarConnector(),
            "check_payments": ReadOnlyPaymentConnector(),
            "search_info": SearchConnector(),
            "get_weather": WeatherConnector(),
            "search_gif": GiphyConnector(),
            "convert_currency": CurrencyConnector(),
            "send_message": WhatsAppConnector(),
            "send_whatsapp": WhatsAppConnector(),
            "send_email": GmailConnector(),
            "set_reminder": ReminderConnector(),
            "check_quotas": QuotaConnector(),
            "check_balance": ReadOnlyPaymentConnector(),
            "list_transactions": ReadOnlyPaymentConnector(),
            "check_invoice_status": ReadOnlyPaymentConnector(),
            "create_invoice": InvoiceConnector(),
            "create_payment_link": IntersendPayConnector(),
            "withdraw": IntersendPayConnector(),
            "check_status": IntersendPayConnector(),
            "search_buses": TravelBusesConnector(),
            "search_hotels": TravelHotelsConnector(),
            "search_flights": TravelFlightsConnector(),
            "search_transfers": TravelTransfersConnector(),
            "search_events": TravelEventsConnector(),
            "create_itinerary": ItineraryConnector(),
            "view_itinerary": ItineraryConnector(),
            "add_to_itinerary": ItineraryConnector(),
            "book_travel_item": ItineraryConnector(),
        }
    except Exception as exc:
        logger.warning("Failed to load legacy connectors: %s", exc)
        return {}


def _scan_connectors_directory() -> list:
    """
    Scan Backend/orchestration/connectors/ for BaseConnector subclasses
    that have the new-style `name` and `actions` attributes.
    """
    from orchestration.base_connector import BaseConnector

    connectors = []
    connectors_dir = Path(__file__).parent / "connectors"

    if not connectors_dir.is_dir():
        return connectors

    for module_info in pkgutil.iter_modules([str(connectors_dir)]):
        if module_info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(
                f"orchestration.connectors.{module_info.name}"
            )
        except Exception as exc:
            logger.debug("Skipping connector module %s: %s", module_info.name, exc)
            continue

        for _attr_name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseConnector)
                and obj is not BaseConnector
                and getattr(obj, "name", "")
                and getattr(obj, "actions", [])
            ):
                try:
                    instance = obj()
                    connectors.append(instance)
                except Exception as exc:
                    logger.warning(
                        "Failed to instantiate connector %s: %s",
                        obj.__name__, exc,
                    )

    return connectors


def _scan_entry_points() -> list:
    """
    Discover pip-installed connectors via 'kazi.connectors' entry point group.

    Community connectors register via pyproject.toml:
        [project.entry-points."kazi.connectors"]
        my_connector = "my_package:MyConnector"
    """
    from orchestration.base_connector import BaseConnector

    connectors = []
    try:
        if hasattr(importlib.metadata, "entry_points"):
            eps = importlib.metadata.entry_points()
            # Python 3.12+ returns a SelectableGroups; 3.9-3.11 returns a dict
            if isinstance(eps, dict):
                group = eps.get("kazi.connectors", [])
            else:
                group = eps.select(group="kazi.connectors")
            for ep in group:
                try:
                    cls = ep.load()
                    if isinstance(cls, type) and issubclass(cls, BaseConnector):
                        connectors.append(cls())
                except Exception as exc:
                    logger.warning("Failed to load entry point %s: %s", ep.name, exc)
    except Exception as exc:
        logger.debug("Entry point discovery unavailable: %s", exc)

    return connectors
