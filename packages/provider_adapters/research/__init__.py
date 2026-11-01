from .browser_readback import BrowserFallbackWebFetchAdapter, BrowserReadbackResearchAdapter
from .discovery import CryptoMacroDiscoveryPolicy
from .document_readback import DocumentReadbackResearchAdapter
from .document_store import ContentAddressedDocumentStore, StoredDocument
from .documents import FetchedDocument, HttpDocumentResearchAdapter
from .fact_pack import CryptoMacroFactPack, FactPackAssessment, FactRequirement
from .g2_replay import FactReplayManifest, FactReplayVariant
from .replay import (
    ReplayResearchArchive,
    ReplayResearchCapabilityAdapter,
    load_replay_archive,
    load_replay_fixtures,
)
from .source_registry import (
    ResearchSourceRegistry,
    ResearchSourceRegistryError,
    SourceLocatorDecision,
)
from .web_search import WebSearchResearchAdapter

__all__ = [
    "CryptoMacroDiscoveryPolicy",
    "FetchedDocument",
    "HttpDocumentResearchAdapter",
    "ContentAddressedDocumentStore",
    "StoredDocument",
    "DocumentReadbackResearchAdapter",
    "BrowserReadbackResearchAdapter",
    "BrowserFallbackWebFetchAdapter",
    "ReplayResearchCapabilityAdapter",
    "ReplayResearchArchive",
    "WebSearchResearchAdapter",
    "CryptoMacroFactPack",
    "FactPackAssessment",
    "FactRequirement",
    "FactReplayManifest",
    "FactReplayVariant",
    "load_replay_archive",
    "load_replay_fixtures",
    "ResearchSourceRegistry",
    "ResearchSourceRegistryError",
    "SourceLocatorDecision",
]
