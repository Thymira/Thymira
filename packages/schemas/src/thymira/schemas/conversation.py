"""Persistent conversation turn metadata shared by runtime and clients."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AfterValidator, Field

from thymira.schemas.base import ThymiraModel
from thymira.schemas.ids import Id
from thymira.schemas.model_input import ModelInput

if TYPE_CHECKING:
    from collections.abc import Sequence

MAX_CLARIFICATION_CHARS = 8000


class ModelWorkIntent(StrEnum):
    """Human-resolved model work, separate from an LLM's proposed plan label."""

    GENERAL = "general"
    EVALUATE_EXISTING = "evaluate_existing"
    TRAIN_NEW = "train_new"
    CLARIFY = "clarify"


_MODEL_TERMS = (
    r"train(?:s|ed|ing)?|retrain(?:s|ed|ing)?|model(?:s|ing|ling)?|"
    r"entren(?:ar|a|e|es|en|amos|aron|ando|ado|ada|amiento)|"
    r"reentren(?:ar|a|e|es|en|amos|ando|ado)|ajust(?:ar|e|a|es|ando)|"
    r"model(?:o|os|ar|a|an|ado|ada|ados|adas)|classifier(?:s)?|classification|classify|"
    r"clasific(?:ar|a|an|ado|ada|aci[oó]n|ador)|regression|regresi[oó]n|regresi[oó]nes|"
    r"predict(?:ion|ions|ive|ing|s)?|predec(?:ir|e|en)|predic(?:e|en|ci[oó]n|ciones)|"
    r"fit(?:s|ting)?|tune|tunes|tuning|tuned|hyperparameter(?:s)?|hiperpar[aá]metros?|"
    r"grid\s+search|random\s+search|b[uú]squeda\s+(?:en\s+)?(?:rejilla|aleatoria)|"
    r"random\s+forest|bosque\s+aleatorio|machine\s+learning|aprendizaje\s+autom[aá]tico|"
    r"experimento(?:s)?|experiment(?:s)?"
)
_MODEL_TERMS_RE = re.compile(rf"\b(?:{_MODEL_TERMS})\b", re.IGNORECASE)
_NEGATED_SENTENCE_START_RE = re.compile(
    r"\b(?:do\s+not|don['\u2019]t|never|no\s+entrenes|nunca\s+entrenes)\b",
    re.IGNORECASE,
)
_DIRECT_MODEL_MODIFIER = (
    r"any|a|an|the|el|la|los|las|one|new|additional|further|more|separate|explicit|first|fresh|"
    r"actually|predictive|statistical|machine|learning|classification|logistic|linear|neural|"
    r"network|deep|supervised|unsupervised|un|una|unos|unas|ning[uú]n|ninguna|nuevo|nueva|"
    r"nuevos|nuevas|quiero|queremos|necesito|necesitamos|debo|debemos|debe|deber[ií]a|"
    r"hace|falta|need|to|que|use|usar|hacer|crear|construir|necesidad|de|querer|queramos|se|"
    r"requiere|ajuste|ajustes|ajustar|ajusta|ajustando|basic|baseline|b[aá]sico|simple"
)
_NEGATED_DIRECT_MODEL_START_RE = re.compile(
    rf"\b(?:without|no|sin)\b"
    rf"(?=[\s:-]*(?:(?:{_DIRECT_MODEL_MODIFIER})\s+){{0,5}}\b(?:{_MODEL_TERMS})\b)",
    re.IGNORECASE,
)
_NEGATED_PASSIVE_TRAINING_START_RE = re.compile(
    r"\b(?:no|never|not)\s+(?:(?:should|must|debe|deber[ií]a)\s+)?"
    r"(?:be|ser|sea|est[eé])\s+(?:trained|fitted|entrenad[oa]s?)\b",
    re.IGNORECASE,
)
_SCOPE_BOUNDARY_RE = re.compile(
    r"[,.!?;\n]"
    r"|\b(?:but|however|except|yet|so|therefore|then|while|whereas|although|though|"
    r"when|during|before|after|pero|sino|aunque|mientras|cuando|entonces|despu[eé]s|"
    r"mientras\s+que)\b"
    r"|\band\s+(?:(?:we|you)\s+)?(?:need|must|should|will|want|plan|intend)\s+to\b",
    re.IGNORECASE,
)
_DISCOURSE_ONLY_RE = re.compile(r"\b(?:however|nevertheless|nonetheless)\b", re.IGNORECASE)
_EDA_SCOPE_RE = re.compile(
    r"\b(?:eda|exploratory\s+data\s+analysis|exploratory\s+analysis|data\s+exploration|"
    r"an[aá]lisis\s+exploratorio|an[aá]lisis\s+descriptivo|exploraci[oó]n\s+de\s+datos|"
    r"descriptive\s+(?:data\s+)?analysis|data\s+profil(?:e|ing)|dataset\s+profil(?:e|ing)|"
    r"profile\s+(?:the\s+)?(?:dataset|data)|"
    r"perfila(?:r|a|e|ando|ado)?\s+(?:el\s+)?(?:dataset|conjunto|datos)|"
    r"explora(?:r|s|mos|n|ndo)?\s+(?:el\s+)?(?:dataset|conjunto|datos))\b",
    re.IGNORECASE,
)
_TRAINING_REQUEST_RE = re.compile(
    r"\b(?:train(?:s|ing)?|retrain(?:s|ing)?|fit(?:ting)?|tune|tunes|tuning|"
    r"entren(?:ar|a|e|es|en|amos|ando|amiento)|"
    r"reentren(?:ar|a|e|es|en|amos|ando)|"
    r"ajust(?:ar|a|e|es|en|ando|e)\s+(?:los\s+)?hiperpar[aá]metros?)\b",
    re.IGNORECASE,
)
_PASSIVE_TRAINING_REQUEST_RE = re.compile(
    r"\b(?:should|must|needs?\s+to|need\s+to|want(?:s)?\s+to|will)\s+be\s+"
    r"(?:trained|fitted)\b|\b(?:que\s+)?(?:sea|est[eé]|debe\s+ser|necesita\s+ser)\s+"
    r"entrenad[oa]s?\b",
    re.IGNORECASE,
)
_TUNING_REQUEST_RE = re.compile(
    r"\b(?:run|perform|do|conduct|execute|use|haz|haga|realiza|realice|ejecuta|aplica)\s+"
    r"(?:(?:a|an|the|una|un|la)\s+)?(?:grid\s+search|random\s+search|"
    r"hyperparameter\s+search|b[uú]squeda\s+(?:en\s+)?(?:rejilla|aleatoria))\b",
    re.IGNORECASE,
)
_MODEL_NOUN = r"(?:model|modelo|classifier|clasificador|regression|regresi[oó]n)"
_MODEL_PURPOSE_SUFFIX = (
    r"(?!\s+(?:evaluation|report|metrics|performance|evaluaci[oó]n|informe|m[eé]tricas)\b"
    r"|\s+(?:with|con)\s+(?:more|m[aá]s\s+)?(?:metrics|m[eé]tricas)\b)"
)
_CREATE_MODEL_RE = re.compile(
    rf"\b(?:build|create|make|develop|haz|hacer|realiza(?:r)?|crea(?:r)?|construye|"
    rf"construir|desarrolla(?:r)?)\b\s+"
    rf"(?:(?:a|an|the|un|una|el|la|otro|nuev[oa]s?|new|another|basic|baseline|"
    rf"b[aá]sico|predictive|predictivo|simple|logistic|log[ií]stic[oa])\s+){{0,6}}"
    rf"{_MODEL_NOUN}\b{_MODEL_PURPOSE_SUFFIX}",
    re.IGNORECASE,
)
_NEED_MODEL_RE = re.compile(
    rf"\b(?:need|want|would\s+like|necesito|necesitamos|quiero|queremos)\b"
    rf"(?:\s+to)?\s+(?:(?:a|an|the|un|una|el|la|new|another|basic|baseline|"
    rf"b[aá]sico|predictive|predictivo|simple|logistic|log[ií]stic[oa])\s+){{0,5}}"
    rf"{_MODEL_NOUN}\b{_MODEL_PURPOSE_SUFFIX}",
    re.IGNORECASE,
)
_EVALUATION_REQUEST_RE = re.compile(
    r"\b(?:evaluate|evaluates|evaluating|evaluation|assess|score|compare|comparing|"
    r"calculate|calculating|"
    r"metrics?|performance|benchmark|eval[uú](?:a|ar|e|amos|en|aci[oó]n)|"
    r"calcula(?:r|a|e|mos|n)|m[eé]tricas?|rendimiento|compara(?:r|a|e|mos|n)|"
    r"comparaci[oó]n|punt[uú]a(?:r|a|e)?)\b",
    re.IGNORECASE,
)
_EXPLANATION_ONLY_RE = re.compile(
    r"^\s*(?:please\s+)?(?:explain|could\s+you\s+explain|can\s+you\s+explain|"
    r"describe|define|tell\s+me\s+what|what\s+(?:is|does)|"
    r"how\s+(?:does|do\s+i|can\s+i)|why\b|qu[eé]\s+(?:significa|es|hace)|"
    r"c[oó]mo\s+(?:funciona|se\s+entrena|se\s+usa)|por\s+qu[eé]|"
    r"expl[ií]ca(?:me|r)?|descr[ií]beme|define\s+)",
    re.IGNORECASE,
)
_EXPLANATION_VERB_RE = re.compile(
    r"\b(?:explain|expl[ií]ca(?:r|me|s)?|describe|descr[ií]be(?:me|s)?|define|"
    r"what\s+does\s+.+\s+mean|qu[eé]\s+significa)\b",
    re.IGNORECASE,
)
_CURRENT_SCOPE_MARKER_RE = re.compile(
    r"(?:[.;,\n]\s*|\b(?:but|pero)\s+)"
    r"(?:instead|en\s+cambio|rather|actually|ahora)\b[,:]?\s+",
    re.IGNORECASE,
)
_FOLLOWING_MODEL_ACTION_RE = re.compile(
    r"\b(?:and|y|then|despu[eé]s|but|pero|instead|en\s+cambio)\b\s*"
    r"(?:please\s+|por\s+favor\s+)?(?:train|retrain|fit|tune|build|create|make|develop|"
    r"entrenar|entrena|reentrenar|realizar|realiza|crear|crea|construir|construye|"
    r"desarrollar|desarrolla|modelar|modela|eval[uú]ar|eval[uú]a|evaluate|compare|compara)\b",
    re.IGNORECASE,
)
_EVALUATION_ENTITY_RE = re.compile(
    rf"\b(?:{_MODEL_NOUN}|prediction|predictions|predicci[oó]n|"
    r"previous\s+model|existing\s+model|modelo\s+existente|modelo\s+anterior)\b",
    re.IGNORECASE,
)
_MODEL_FRAGMENT_RE = re.compile(
    rf"^\s*(?:(?:a|an|the|un|una|el|la|new|nuev[oa]|basic|b[aá]sico|predictive|predictivo)\s+){{0,3}}"
    rf"{_MODEL_NOUN}\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def resolve_model_work_intent(
    message: str,
    clarification_context: Sequence[str] = (),
    *,
    dataset_names: Sequence[str] = (),
) -> ModelWorkIntent:
    """Resolve model-work intent from current human text and its linked human clarifications.

    Model labels emitted by an LLM are deliberately absent from this contract. A terse answer
    inherits the linked request, while an explicit current request or restriction takes effect
    first. Text presented as code or quotation and explicit explanations provide no work scope.
    """
    names = frozenset(name.casefold() for name in dataset_names)
    if message.strip().casefold() in names:
        intent, explicit_scope = ModelWorkIntent.GENERAL, False
    else:
        intent, explicit_scope, _ = _single_model_work_intent(message)
    prior_values = tuple(
        _single_model_work_intent(prior)
        for prior in clarification_context
        if prior.strip().casefold() not in names
    )
    linked_prohibition = any(item[2] for item in prior_values)
    if intent is ModelWorkIntent.TRAIN_NEW and linked_prohibition:
        return ModelWorkIntent.CLARIFY
    if intent is not ModelWorkIntent.GENERAL or explicit_scope:
        return intent

    for intent, explicit_scope, _ in reversed(prior_values):
        if intent is ModelWorkIntent.TRAIN_NEW and linked_prohibition:
            return ModelWorkIntent.CLARIFY
        if intent is not ModelWorkIntent.GENERAL or explicit_scope:
            return intent
    return ModelWorkIntent.GENERAL


def is_explanatory_model_request(message: str) -> bool:
    """Identify model explanations that must stay outside Run publication."""
    visible = _human_model_text(message)
    if not _MODEL_TERMS_RE.search(message):
        return False
    explanation = _EXPLANATION_ONLY_RE.search(visible) or _EXPLANATION_VERB_RE.search(visible)
    if explanation is None:
        return False
    training = next(
        (
            match
            for pattern in (_TRAINING_REQUEST_RE, _CREATE_MODEL_RE, _NEED_MODEL_RE)
            if (match := pattern.search(visible)) is not None
        ),
        None,
    )
    if training is not None and training.start() < explanation.start():
        return False
    return not _FOLLOWING_MODEL_ACTION_RE.search(visible)


def model_work_appears_only_in_quoted_text(message: str) -> bool:
    """Identify action-looking model text that appears only inside quotes or code."""
    visible = _human_model_text(message)
    return (
        bool(_MODEL_TERMS_RE.search(message))
        and not _MODEL_TERMS_RE.search(visible)
        and not _EDA_SCOPE_RE.search(visible)
    )


def _single_model_work_intent(message: str) -> tuple[ModelWorkIntent, bool, bool]:
    """Resolve one human message and report whether it explicitly scopes other work."""
    visible = _human_model_text(message)
    markers = tuple(_CURRENT_SCOPE_MARKER_RE.finditer(visible))
    if markers:
        earlier_scope = _single_model_work_intent(visible[: markers[-1].start()])
        later_scope = _single_model_work_intent(visible[markers[-1].end() :])
        if later_scope[1]:
            return later_scope[0], later_scope[1], later_scope[2] or earlier_scope[2]
    if not visible:
        return ModelWorkIntent.GENERAL, False, False
    if is_explanatory_model_request(visible):
        return ModelWorkIntent.GENERAL, True, False

    positive = _mask_negated_scopes(visible, _NEGATED_SENTENCE_START_RE, comma_boundary=False)
    positive = _mask_negated_scopes(positive, _NEGATED_DIRECT_MODEL_START_RE, comma_boundary=True)
    positive = _mask_negated_scopes(
        positive, _NEGATED_PASSIVE_TRAINING_START_RE, comma_boundary=False
    )
    model_terms = bool(_MODEL_TERMS_RE.search(positive))
    has_positive_training = bool(
        _TRAINING_REQUEST_RE.search(positive)
        or _PASSIVE_TRAINING_REQUEST_RE.search(positive)
        or _TUNING_REQUEST_RE.search(positive)
        or _CREATE_MODEL_RE.search(positive)
        or _NEED_MODEL_RE.search(positive)
    )
    prohibits_training = bool(
        not has_positive_training
        and (
            _TRAINING_REQUEST_RE.search(visible)
            or _PASSIVE_TRAINING_REQUEST_RE.search(visible)
            or _TUNING_REQUEST_RE.search(visible)
            or _CREATE_MODEL_RE.search(visible)
            or _NEED_MODEL_RE.search(visible)
        )
    )
    intent = ModelWorkIntent.GENERAL
    explicit_scope = False
    if has_positive_training:
        intent, explicit_scope = ModelWorkIntent.TRAIN_NEW, True
    elif _EVALUATION_REQUEST_RE.search(positive) and _EVALUATION_ENTITY_RE.search(positive):
        intent, explicit_scope = ModelWorkIntent.EVALUATE_EXISTING, True
    elif (
        (prohibits_training and _EVALUATION_REQUEST_RE.search(positive))
        or (
            model_terms
            and re.search(
                r"\b(?:model(?:s|ing|ling)?|model(?:ar|a|an)|use|usar|develop|desarrollar|"
                r"need|want|quiero|necesito|should|must|debe|deber[ií]a|perform|run|hacer|"
                r"produce|generate|predict|predecir)\b",
                positive,
                re.IGNORECASE,
            )
        )
        or _MODEL_FRAGMENT_RE.search(positive)
    ):
        intent, explicit_scope = ModelWorkIntent.CLARIFY, True
    elif _EDA_SCOPE_RE.search(visible) or _MODEL_TERMS_RE.search(visible):
        explicit_scope = True
    return intent, explicit_scope, prohibits_training


def _human_model_text(message: str) -> str:
    """Remove quoted, code and block-quoted text before reading a request as intent."""
    visible = re.sub(r"```[\s\S]*?```|`[^`]*`", " ", message)
    visible = re.sub(r"(?m)^\s*>.*$", " ", visible)
    for opening, closing in (('"', '"'), ("“", "”"), ("‘", "’")):  # noqa: RUF001  # Unicode paired quotes are valid delimiters.
        visible = re.sub(
            re.escape(opening) + r"[^" + re.escape(closing) + r"]*" + re.escape(closing),
            " ",
            visible,
        )
    return re.sub(r"(?<!\w)'[^'\n]*'(?!\w)", " ", visible)


def _scope_has_content(text: str) -> bool:
    """Return whether a would-be negated scope contains more than discourse punctuation."""
    return bool(re.search(r"\w", _DISCOURSE_ONLY_RE.sub(" ", text)))


def _negated_scope_end(prompt: str, content_start: int, *, comma_boundary: bool) -> int:
    """Find the first grammatical boundary after a negator."""
    for match in _SCOPE_BOUNDARY_RE.finditer(prompt, content_start):
        boundary = match.group()
        if boundary == "," and not comma_boundary:
            continue
        if (boundary == "," or boundary[0].isalnum()) and not _scope_has_content(
            prompt[content_start : match.start()]
        ):
            continue
        return match.start()
    return len(prompt)


def _mask_negated_scopes(prompt: str, start_re: re.Pattern[str], *, comma_boundary: bool) -> str:
    """Replace matching negated scopes with spaces while preserving positive clauses."""
    masked = list(prompt)
    for match in start_re.finditer(prompt):
        end = _negated_scope_end(prompt, match.end(), comma_boundary=comma_boundary)
        masked[match.start() : end] = " " * (end - match.start())
    return "".join(masked)


def _bounded_clarifications(messages: tuple[str, ...]) -> tuple[str, ...]:
    """Keep the entire human clarification chain within its shared context ceiling."""
    if len("\n\n".join(messages)) > MAX_CLARIFICATION_CHARS:
        raise ValueError("clarification context exceeds its character limit")
    return messages


ClarificationContext = Annotated[
    tuple[Annotated[str, Field(min_length=1, max_length=8000)], ...],
    Field(max_length=8),
    AfterValidator(_bounded_clarifications),
]


class ConversationInput(ThymiraModel):
    """Immutable request metadata attached to the Run created for one conversation turn."""

    ordinal: int = Field(ge=1)
    client_message_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=16_000)
    context_run_ids: tuple[Id, ...] = Field(default=(), max_length=12)
    clarification_context: ClarificationContext = ()
    work_kind: Literal["general", "evaluate_existing", "train_new"] = "general"
    model_inputs: tuple[ModelInput, ...] = Field(default=(), max_length=16)

    @property
    def requested_work(self) -> str:
        """Return this request's human clarification chain without unrelated past work."""
        if not self.clarification_context:
            return self.message
        return (
            "Earlier user messages clarifying this request (later scope corrections prevail):\n"
            + "\n\n".join(self.clarification_context)
            + f"\n\nLatest user message:\n{self.message}"
        )


class ConversationDiagnostic(ThymiraModel):
    """Bounded operational explanation, separate from an approved assistant response."""

    summary: str = Field(min_length=1, max_length=1000)
    details: tuple[Annotated[str, Field(max_length=1000)], ...] = Field(default=(), max_length=8)
    next_steps: tuple[Annotated[str, Field(max_length=1000)], ...] = Field(default=(), max_length=8)
    policy_rule: str | None = Field(default=None, max_length=128)


__all__ = [
    "ConversationDiagnostic",
    "ConversationInput",
    "ModelWorkIntent",
    "is_explanatory_model_request",
    "model_work_appears_only_in_quoted_text",
    "resolve_model_work_intent",
]
