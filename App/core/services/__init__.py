"""Always-on AVA services (HealthMonitor, CallManager, custom)."""

from core.services.base import AvaService, EventSink, ServiceEvent
from core.services.registry import (
    ServiceRegistry,
    get_service_registry,
    start_ava_services,
    stop_ava_services,
)

__all__ = [
    "AvaService",
    "EventSink",
    "ServiceEvent",
    "ServiceRegistry",
    "get_service_registry",
    "start_ava_services",
    "stop_ava_services",
]
