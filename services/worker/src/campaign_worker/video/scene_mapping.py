from collections.abc import Sequence

from campaign_contracts.artifacts import PublicArtifactReference

from campaign_worker.errors import WorkflowOperationError

_REQUIRED_SCENE_NUMBERS = {1, 2, 3}


def resolve_scene_artifacts(
    image_artifacts: Sequence[PublicArtifactReference],
) -> dict[int, PublicArtifactReference]:
    """Map scene_number -> artifact, never trusting array order.

    Validates that scenes 1, 2, and 3 are each present exactly once and that
    no two scenes resolved to the identical generated image (the real
    production bug this guards: a video that appeared to reuse the same
    visual throughout).
    """
    by_scene: dict[int, PublicArtifactReference] = {}
    for artifact in image_artifacts:
        if artifact.scene_number is None:
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED", "image artifact is missing scene_number", retryable=False
            )
        if artifact.scene_number in by_scene:
            raise WorkflowOperationError(
                "ARTIFACT_VALIDATION_FAILED",
                f"duplicate image artifact for scene {artifact.scene_number}",
                retryable=False,
            )
        by_scene[artifact.scene_number] = artifact

    missing = _REQUIRED_SCENE_NUMBERS - by_scene.keys()
    if missing:
        raise WorkflowOperationError(
            "ARTIFACT_VALIDATION_FAILED",
            f"missing image artifact(s) for scene(s) {sorted(missing)}",
            retryable=False,
        )

    checksums = [artifact.checksum_sha256 for artifact in by_scene.values()]
    if len(set(checksums)) != len(checksums):
        raise WorkflowOperationError(
            "ARTIFACT_VALIDATION_FAILED",
            "two or more scenes resolved to the identical image artifact",
            retryable=False,
        )

    return by_scene
