"""Retry feedback describes schema failures without copying values or dynamic mapping keys."""

import pytest
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from thymira.agents.llm.validation_feedback import validation_feedback


def test_feedback_omits_sensitive_mapping_keys_and_invalid_values() -> None:
    class Output(BaseModel):
        metrics: dict[str, int]

    with pytest.raises(ValidationError) as caught:
        Output.model_validate({"metrics": {"private_key_marker": "private_value_marker"}})
    feedback = validation_feedback(caught.value, Output)
    assert "metrics" in feedback
    assert "metrics.<key>: int_parsing" in feedback
    assert "int_parsing" in feedback
    assert "private_key_marker" not in feedback
    assert "private_value_marker" not in feedback


def test_feedback_keeps_nested_schema_locations_for_wrong_types() -> None:
    class Metric(BaseModel):
        value: int

    class Output(BaseModel):
        primary: Metric
        samples: list[Metric]

    with pytest.raises(ValidationError) as caught:
        Output.model_validate({"primary": {"value": "bad"}, "samples": [{"value": "bad"}]})

    feedback = validation_feedback(caught.value, Output)

    assert "primary.value: int_parsing" in feedback
    assert "samples.0.value: int_parsing" in feedback


def test_feedback_normalizes_mapping_key_that_collides_with_another_field() -> None:
    class Metric(BaseModel):
        value: int

    class Output(BaseModel):
        summary: str
        metrics: dict[str, Metric]

    with pytest.raises(ValidationError) as caught:
        Output.model_validate({"summary": "Overview", "metrics": {"summary": {"value": "bad"}}})

    feedback = validation_feedback(caught.value, Output)

    assert "metrics.<key>.value: int_parsing" in feedback
    assert "metrics.summary.value" not in feedback


def test_feedback_normalizes_numeric_mapping_keys() -> None:
    class Metric(BaseModel):
        value: int

    class Output(BaseModel):
        metrics: dict[int, Metric]

    with pytest.raises(ValidationError) as caught:
        Output.model_validate({"metrics": {12: {"value": "bad"}}})

    feedback = validation_feedback(caught.value, Output)

    assert "metrics.<key>.value: int_parsing" in feedback
    assert "metrics.12" not in feedback


def test_feedback_replaces_custom_validator_codes_and_messages() -> None:
    class Output(BaseModel):
        token: str

        @field_validator("token")
        @classmethod
        def reject_token(cls, value: str) -> str:
            raise PydanticCustomError(
                "private_custom_code_marker",
                "private_validator_message_marker {secret}",
                {"secret": "private_context_marker"},
            )

    with pytest.raises(ValidationError) as caught:
        Output.model_validate({"token": "private_input_marker"})

    feedback = validation_feedback(caught.value, Output)

    assert "token: validation_error" in feedback
    for private_marker in (
        "private_custom_code_marker",
        "private_validator_message_marker",
        "private_context_marker",
        "private_input_marker",
    ):
        assert private_marker not in feedback


def test_feedback_redacts_before_clipping_and_limits_paths_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "synthetic-credential-marker-1234567890"
    monkeypatch.setenv("OPENAI_API_KEY", credential)

    class LongOutput(BaseModel):
        field_0: str = Field(alias="x" * 130 + credential)
        field_1: str
        field_2: str
        field_3: str
        field_4: str
        field_5: str
        field_6: str
        field_7: str
        field_8: str
        field_9: str

    output = LongOutput

    with pytest.raises(ValidationError) as caught:
        output.model_validate({})

    feedback = validation_feedback(caught.value, output)

    assert credential[:12] not in feedback
    assert "[REDACTED:CREDENTIAL]" in feedback
    assert feedback.count(": missing") == 8
    assert "field_8: missing" not in feedback
    assert len(feedback) < 8 * 190
