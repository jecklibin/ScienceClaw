from __future__ import annotations

from pathlib import Path
from typing import Optional

from .models import RecordingArtifact, RecordingRun, RecordingSegment


class ArtifactRegistry:
    def register(self, run: RecordingRun, segment: RecordingSegment, artifact: RecordingArtifact) -> None:
        run.artifact_index.append(artifact)
        segment.artifacts.append(artifact)
        run.updated_at = artifact.created_at

    def latest(
        self,
        run: RecordingRun,
        artifact_type: Optional[str] = None,
        labels: Optional[list[str]] = None,
    ) -> Optional[RecordingArtifact]:
        candidates = run.artifact_index
        if artifact_type:
            candidates = [item for item in candidates if item.type == artifact_type]
        if labels:
            label_set = set(labels)
            candidates = [item for item in candidates if label_set.intersection(item.labels)]
        return candidates[-1] if candidates else None

    def exists(self, artifact: RecordingArtifact) -> bool:
        if artifact.type != "file" or not artifact.path:
            return True
        return Path(artifact.path).exists()

    def build_imports(self, **bindings: RecordingArtifact) -> dict[str, str]:
        imports: dict[str, str] = {}
        for name, artifact in bindings.items():
            if artifact.path:
                imports[name] = artifact.path
            elif artifact.value is not None:
                imports[name] = str(artifact.value)
        return imports
