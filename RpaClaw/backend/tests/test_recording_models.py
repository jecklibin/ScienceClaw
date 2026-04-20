import tempfile
from pathlib import Path

from backend.recording.artifact_registry import ArtifactRegistry
from backend.recording.models import RecordingArtifact, RecordingRun, RecordingSegment


def test_artifact_registry_returns_latest_file_artifact():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run = RecordingRun(id="run-1", session_id="session-1", user_id="u1")
        segment = RecordingSegment(id="seg-1", run_id=run.id, kind="rpa", intent="下载 PDF")
        file_path = Path(tmp_dir) / "paper.pdf"
        file_path.write_text("pdf", encoding="utf-8")
        artifact = RecordingArtifact(
            id="artifact-1",
            run_id=run.id,
            segment_id=segment.id,
            name="downloaded_pdf",
            type="file",
            path=str(file_path),
            labels=["download", "pdf"],
        )

        registry = ArtifactRegistry()
        registry.register(run, segment, artifact)

        latest = registry.latest(run, artifact_type="file")
        assert latest is not None
        assert latest.name == "downloaded_pdf"
        assert registry.exists(latest) is True
