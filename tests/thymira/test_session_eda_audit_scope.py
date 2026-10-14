"""A checked EDA summary cannot make unrelated narrative reports pass fidelity checks."""

from thymira.events import InMemoryEventLog
from thymira.mira.checks import ControlStatus
from thymira.mira.checks.report import check_report_fidelity
from thymira.schemas import ArtifactKind, new_id
from thymira.state import ArtifactWrite, LocalArtifactStore
from thymira.tools import ToolInvocation
from thymira.tools.builtins.data_analysis import ProfileDataset
from thymira.tools.datasets import register_dataset


def test_linked_eda_report_does_not_certify_an_unlinked_report(tmp_path):
    run_id, agent_id = new_id("run"), new_id("agent")
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    source = tmp_path / "sample.csv"
    source.write_text("age,label\n24,yes\n31,no\n", encoding="utf-8")
    register_dataset(store, source, "sample", produced_by=agent_id)
    ProfileDataset().execute(
        ToolInvocation(run_id, agent_id, tmp_path, store),
        {
            "dataset": "sample",
            "description": "Profile the sample",
        },
    )
    profile = store.get("profile/sample.json")
    assert profile is not None
    store.save_artifact_batch(
        (
            ArtifactWrite(
                name="checked.md",
                data=b"The profile has 2 rows.",
                kind=ArtifactKind.REPORT,
                media_type="text/markdown",
                input_artifact_ids=(profile.id,),
            ),
        ),
        produced_by=agent_id,
    )
    store.save_text(
        "unchecked.md", "The accuracy is 99.9%.", produced_by=agent_id, kind=ArtifactKind.REPORT
    )
    status, detail = check_report_fidelity(store, InMemoryEventLog(run_id).events())
    assert status is ControlStatus.NOT_APPLICABLE, detail
    assert "unchecked.md" in detail
