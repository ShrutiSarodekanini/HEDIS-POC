"""
Adapter registry for managing and discovering source adapters.

Provides central registry to register, lookup, and list available adapters.
Pipeline uses registry to dynamically select adapter based on adapter_name.
"""

from __future__ import annotations

from typing import Type

from .base import SourceAdapter
from .ccda_hie_adapter import CCDAdapter
from .claims_837_adapter import Claims837Adapter
from .ncpdp_pharmacy_adapter import NCPDPPharmacyAdapter
from .ehr_fhir_adapter import EHRFHIRAdapter
from .synthea_adapter import SyntheaCSVAdapter


class AdapterRegistry:
    """Central registry for all data source adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, Type[SourceAdapter]] = {}

    def register(self, adapter_class: Type[SourceAdapter]) -> None:
        """
        Register an adapter class.
        
        Args:
            adapter_class: Subclass of SourceAdapter to register
        
        Raises:
            ValueError: If adapter_name already registered
        """
        instance = adapter_class()
        adapter_name = instance.adapter_name
        
        if adapter_name in self._adapters:
            raise ValueError(f"Adapter '{adapter_name}' already registered")
        
        self._adapters[adapter_name] = adapter_class

    def get_adapter(self, adapter_name: str) -> SourceAdapter | None:
        """
        Get adapter instance by name.
        
        Args:
            adapter_name: Name of adapter to retrieve
        
        Returns:
            SourceAdapter instance or None if not found
        """
        adapter_class = self._adapters.get(adapter_name)
        return adapter_class() if adapter_class else None

    def list_adapters(self) -> list[str]:
        """List all registered adapter names."""
        return sorted(list(self._adapters.keys()))

    def has_adapter(self, adapter_name: str) -> bool:
        """Check if adapter is registered."""
        return adapter_name in self._adapters

    def __repr__(self) -> str:
        adapters = ", ".join(self.list_adapters())
        return f"<AdapterRegistry: {adapters}>"


# Global registry instance
registry = AdapterRegistry()

# Register default adapters
registry.register(SyntheaCSVAdapter)
registry.register(EHRFHIRAdapter)
registry.register(CCDAdapter)
registry.register(Claims837Adapter)
registry.register(NCPDPPharmacyAdapter)
