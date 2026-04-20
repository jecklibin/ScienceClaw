from __future__ import annotations

from .artifact_registry import ArtifactRegistry
from .orchestrator import RecordingOrchestrator


recording_orchestrator = RecordingOrchestrator()
artifact_registry = ArtifactRegistry()
