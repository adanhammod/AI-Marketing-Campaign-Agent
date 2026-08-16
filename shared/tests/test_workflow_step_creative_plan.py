import re
from pathlib import Path

from campaign_contracts.enums import CampaignStatus, WorkflowStep

ROOT = Path(__file__).parents[2]


def test_workflow_step_has_creative_plan_member():
    assert WorkflowStep.CREATIVE_PLAN == "creative_plan"


def test_creative_plan_does_not_require_lifecycle_doc_sync():
    # test_document_enums.py::test_documented_enum_values_match only cross-checks
    # CampaignStatus, ArtifactType, ErrorComponent, and CampaignEventType against
    # docs/contracts/*.md -- WorkflowStep is not part of that sync surface (verified
    # by reading test_document_enums.py directly), so adding WorkflowStep.CREATIVE_PLAN
    # requires no docs update for that automated check. This test pins that fact so a
    # future change to the sync surface can't silently go unnoticed.
    source = (ROOT / "shared" / "tests" / "test_document_enums.py").read_text(encoding="utf-8")
    checked_enums = set(re.findall(r"values\(([A-Z]\w+)\)", source))
    assert checked_enums == {"CampaignStatus", "ArtifactType", "ErrorComponent", "CampaignEventType"}
    assert "WorkflowStep" not in checked_enums


def test_campaign_status_lifecycle_diagram_has_no_workflow_step_values():
    # The lifecycle doc's mermaid diagram enumerates CampaignStatus transitions, not
    # WorkflowStep values -- confirms there is no separate prose listing of the
    # step pipeline order in that doc that would need updating for CREATIVE_PLAN.
    lifecycle = (ROOT / "docs" / "contracts" / "campaign-lifecycle.md").read_text(encoding="utf-8")
    assert "creative_plan" not in lifecycle.lower()
    for status in CampaignStatus:
        # sanity: the doc really does talk about CampaignStatus, so the "no
        # WorkflowStep prose" conclusion above isn't just an empty/wrong file.
        if status in {CampaignStatus.GENERATING_STRATEGY, CampaignStatus.GENERATING_STORYBOARD}:
            assert status.value in lifecycle
