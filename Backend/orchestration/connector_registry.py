"""
Connector auto-discovery and registration.

Scans the connectors directory for BaseConnector subclasses and builds
a unified action-to-connector map. Also loads legacy hardcoded connectors
for backward compatibility, and scans the top-level `examples/connectors/`
directory for example-style connectors when demo mode is enabled.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import logging
import os
import pkgutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Singleton caches
_discovered_connectors: Optional[Dict[str, Any]] = None
_registered_catalog_entries: List[Dict[str, Any]] = []
_contract_violations: List[Dict[str, Any]] = []


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _validate_or_warn(connector_name: str, entry: Any) -> tuple:
    """Validate a catalog entry; log a warning and return (False, errors)
    on failure so the caller can skip the entry. See
    docs/contracts/tool-schema.md.

    Violations are also recorded in ``_contract_violations`` so tests (and
    operators) can sweep the whole live registry for contract drift instead
    of relying on warnings scrolling past at boot.
    """
    from orchestration.contracts import validate_catalog_entry

    ok, errors = validate_catalog_entry(entry)
    if not ok:
        action = entry.get("action") if isinstance(entry, dict) else "<unknown>"
        logger.warning(
            "Connector %s: catalog entry for action %r violates tool-schema "
            "contract v1.0 and will be skipped: %s",
            connector_name, action, "; ".join(errors),
        )
        _contract_violations.append({
            "connector": connector_name,
            "action": action,
            "errors": list(errors),
        })
    return ok, errors


def _warn_if_not_async(connector) -> None:
    """Warn when a connector's execute() isn't a coroutine function.

    The agent loop only awaits tool calls, so a sync ``def execute`` will
    always raise at runtime. Catch it at registration with a clear hint.
    """
    execute = getattr(connector, "execute", None)
    if execute is not None and not inspect.iscoroutinefunction(execute):
        logger.warning(
            "Connector %s: execute() must be `async def`; a synchronous "
            "execute() will raise when the agent loop awaits it.",
            getattr(connector, "name", None) or connector.__class__.__name__,
        )


def _warn_on_conflict(connector_map: Dict[str, Any], action_name: str, new_name: str) -> None:
    """Warn when a connector overwrites an action owned by another connector."""
    existing = connector_map.get(action_name)
    if existing is not None and getattr(existing, "name", None) != new_name:
        logger.warning(
            "Action %r is already registered by connector %s; %s overrides it.",
            action_name,
            getattr(existing, "name", None) or existing.__class__.__name__,
            new_name,
        )


def _warn_on_actions_mismatch(connector, entries: List[Dict[str, Any]]) -> None:
    """Warn when a connector's `actions` list and catalog entries disagree.

    An action in `actions` with no catalog entry never surfaces as an LLM
    tool; a catalog entry not listed in `actions` never routes at runtime.
    """
    name = getattr(connector, "name", None) or connector.__class__.__name__
    declared = set(connector.actions or [])
    catalogued = {entry.get("action") for entry in entries if isinstance(entry, dict)}
    if declared - catalogued:
        logger.warning(
            "Connector %s: actions %s are declared but have no catalog entry, "
            "so the LLM will never see them as tools.",
            name, sorted(declared - catalogued),
        )
    if catalogued - declared:
        logger.warning(
            "Connector %s: catalog entries %s are not in `actions`, so they "
            "will not be routable at execution time.",
            name, sorted(catalogued - declared),
        )


def _register_actions_and_entries(
    connector_map: Dict[str, Any], connector,
    *, allow_override: bool = True,
) -> List[Dict[str, Any]]:
    """Register one connector's actions + catalog entries, with guardrails.

    ``allow_override=False`` (entry-point connectors) refuses to shadow an
    action already owned by a different connector — a pip-installed package
    must never silently replace a built-in's implementation. Returns the
    validated catalog entries (invalid ones are skipped with a warning).
    Callers merge them into `_registered_catalog_entries` only for
    connectors whose entries should surface in the global action catalog.
    """
    name = getattr(connector, "name", None) or connector.__class__.__name__
    _warn_if_not_async(connector)

    for action_name in connector.actions:
        existing = connector_map.get(action_name)
        if (
            not allow_override
            and existing is not None
            and getattr(existing, "name", None) != name
        ):
            logger.warning(
                "Entry-point connector %s tried to shadow action %r owned by "
                "built-in connector %s; registration skipped for that action.",
                name, action_name,
                getattr(existing, "name", None) or existing.__class__.__name__,
            )
            continue
        _warn_on_conflict(connector_map, action_name, name)
        connector_map[action_name] = connector

    try:
        raw_entries = connector.get_action_catalog_entries()
    except Exception as exc:
        logger.warning(
            "Connector %s: get_action_catalog_entries() failed: %s",
            name, exc,
        )
        raw_entries = []

    validated: List[Dict[str, Any]] = []
    for entry in raw_entries:
        ok, _errors = _validate_or_warn(name, entry)
        if ok:
            validated.append(entry)

    _warn_on_actions_mismatch(connector, validated)
    return validated


def is_demo_mode() -> bool:
    """KAZI_DEMO_MODE — boots the runtime with example connectors enabled,
    no real credentials required, banner logged at startup. The single
    flag readers should check; everything demo-related branches off it.
    """
    return _env_flag_enabled("KAZI_DEMO_MODE", default=False)


def _examples_connectors_root() -> Optional[Path]:
    """Locate `<repo_root>/examples/connectors/`.

    The repo root sits two levels above this file:
    `Backend/orchestration/connector_registry.py` -> repo root.
    """
    candidate = Path(__file__).resolve().parents[2] / "examples" / "connectors"
    return candidate if candidate.is_dir() else None


def discover_connectors() -> Dict[str, Any]:
    """
    Build the action-to-connector map by:
    1. Loading legacy hardcoded connectors (backward compat)
    2. Scanning connectors/ directory for new-style BaseConnector subclasses
    3. Scanning installed packages for 'kazi.connectors' entry points

    New-style connectors override legacy ones if there's a conflict.
    """
    global _discovered_connectors

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

        entries = _register_actions_and_entries(connector_map, connector)
        _registered_catalog_entries.extend(entries)

        logger.info("Registered connector: %s v%s (%d actions)",
                    connector.name, connector.version, len(connector.actions))

    # Step 2b: When demo mode is on, also scan the top-level examples/
    # directory so the canonical "copy this to start" connector boots
    # with no opt-in required.
    if is_demo_mode():
        # Example connectors are routing-only: they go into the connector_map
        # for direct invocation but NOT into the global action_catalog, so the
        # workflow executor's unified registry dispatch never surfaces them as
        # workflow steps.
        # Demo workflows that need example actions invoke the connector
        # directly through the registry, not through the workflow executor's
        # action-catalog dispatch.
        for connector in _scan_examples_directory():
            ok, msg = connector.validate_config()
            if not ok:
                logger.warning("Example connector %s skipped: %s", connector.name, msg)
                continue
            # Example connectors are routing-only: register actions + validate
            # entries, but do NOT add them to the global action catalog.
            _register_actions_and_entries(connector_map, connector)
            logger.info(
                "Registered example connector: %s v%s (%d actions, "
                "routing-only — not in action catalog)",
                connector.name, connector.version, len(connector.actions),
            )

    # Step 3: Scan entry points (for pip-installed community connectors).
    # Shadowing is refused: community packages may add new actions but can
    # never replace a built-in connector's implementation of an existing one.
    entrypoint_connectors = _scan_entry_points()
    for connector in entrypoint_connectors:
        ok, msg = connector.validate_config()
        if not ok:
            logger.warning("Entry-point connector %s skipped: %s", connector.name, msg)
            continue
        entries = _register_actions_and_entries(
            connector_map, connector, allow_override=False,
        )
        _registered_catalog_entries.extend(entries)
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


def get_contract_violations() -> List[Dict[str, Any]]:
    """Return contract violations recorded during the last discovery."""
    return list(_contract_violations)


def reset_registry() -> None:
    """Clear the registry (useful for testing)."""
    global _discovered_connectors, _registered_catalog_entries, _contract_violations
    _discovered_connectors = None
    _registered_catalog_entries = []
    _contract_violations = []


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
        from orchestration.tool_router import (
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
            "remove_from_itinerary": ItineraryConnector(),
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


def _scan_examples_directory() -> list:
    """Scan `<repo_root>/examples/connectors/*/` for BaseConnector subclasses.

    Each example connector lives in its own subdirectory and ships at
    least one `*_connector.py` file. Loaded via importlib.util so the
    examples don't need to be on sys.path or installed as a package.
    Only invoked when `is_demo_mode()` is true.
    """
    from orchestration.base_connector import BaseConnector

    connectors: list = []
    examples_root = _examples_connectors_root()
    if examples_root is None:
        logger.debug("Examples directory not found; skipping example scan.")
        return connectors

    for sub in sorted(p for p in examples_root.iterdir() if p.is_dir()):
        if sub.name.startswith("_") or sub.name.startswith("."):
            continue
        for py_file in sorted(sub.glob("*_connector.py")):
            module_name = f"kazi_examples_{sub.name}_{py_file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as exc:
                logger.warning("Skipping example %s/%s: %s", sub.name, py_file.name, exc)
                continue

            for _attr_name, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseConnector)
                    and obj is not BaseConnector
                    and getattr(obj, "name", "")
                    and getattr(obj, "actions", [])
                ):
                    try:
                        connectors.append(obj())
                    except Exception as exc:
                        logger.warning(
                            "Failed to instantiate example connector %s: %s",
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
                        if not getattr(cls, "name", "") or not getattr(cls, "actions", []):
                            logger.warning(
                                "Entry point %s: connector must define a non-empty "
                                "'name' and 'actions'; skipping.",
                                ep.name,
                            )
                            continue
                        connectors.append(cls())
                except Exception as exc:
                    logger.warning("Failed to load entry point %s: %s", ep.name, exc)
    except Exception as exc:
        logger.debug("Entry point discovery unavailable: %s", exc)

    return connectors
