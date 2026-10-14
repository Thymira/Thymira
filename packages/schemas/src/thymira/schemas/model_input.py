"""Immutable prior-model identity carried by a conversation work request."""

from pydantic import Field

from thymira.schemas.base import ThymiraModel
from thymira.schemas.ids import Id


class ModelInput(ThymiraModel):
    """A verified model revision and its recorded evaluation inputs, never a workspace path."""

    source_run_id: Id
    model_artifact_id: Id
    model_name: str = Field(min_length=1, max_length=1024)
    model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_artifact_id: Id
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_artifact_id: Id
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_name: str = Field(min_length=1, max_length=1024)
    target_column: str = Field(min_length=1, max_length=1024)
    training_seq: int = Field(ge=0)
    training_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def model_alias(self) -> str:
        """Return a collision-free logical name in the consuming Run."""
        return f"prior/{self.source_run_id}/{self.model_artifact_id}.joblib"

    @property
    def dataset_alias(self) -> str:
        """Return the Run-local registered dataset identity."""
        return f"prior-{self.dataset_artifact_id}"

    @property
    def context_alias(self) -> str:
        """Return the immutable evaluation context name in the consuming Run."""
        return f"prior/{self.source_run_id}/{self.model_artifact_id}.evaluation.json"
