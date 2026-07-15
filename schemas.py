import datetime
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Optional


class QBankValidationError(ValueError):
    """A QBank dataclass has an incorrect value"""


class QBankMultipleSkills(ValueError):
    """Returned if multiple skills arised from a simple QBank search"""


@dataclass(frozen=True)
class Assessment:
    id: int
    name: str


@dataclass(frozen=True)
class TestModule:
    id: int
    name: str
    domains: tuple["Domain", ...]


@dataclass(frozen=True)
class Skill:
    id: int
    name: str


@dataclass(frozen=True)
class Domain:
    id: int
    name: str
    code: str
    skills: tuple[Skill, ...]


@dataclass(frozen=True, slots=True)
class QuestionSummary:
    assessment: Assessment
    domain: Domain
    skill: Skill
    external_id: Optional[uuid.UUID]
    uuid: uuid.UUID
    question_id: str
    difficulty: Literal["E", "M", "H"]
    score_band: int
    last_updated_date: datetime.date
    created_date: datetime.date


@dataclass(frozen=True, slots=True)
class DetailedQuestion:
    stem: str
    stimulus: str | None
    type: Literal["mcq", "spr"]
    rationale: str
    question_summary: QuestionSummary
    answers: tuple["Answer", ...]
    correct_answers: tuple["Answer", ...]


@dataclass(frozen=True)
class Answer:
    id: str | uuid.UUID | None
    content: str


@dataclass(frozen=True)
class QBankDownloadProgress:
    status: Literal["COMPLETED", "IN_PROGRESS"]
    download_url: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class QBankLiveDownloadResults(QBankDownloadProgress):
    status: Literal["IN_PROGRESS"] = field(default="IN_PROGRESS", init=False)
    questions_processed: int
    total_questions: int
    estimated_time_remaining: datetime.timedelta


class QBankPDFStyle(StrEnum):
    ANSWERS_AND_EXPLANATIONS = "with-ans-and-expl"
    NO_ANSWER_NO_HEADER = "no-ans-no-hdr"
    NO_ANSWER_OR_EXPL = "no-ans-no-expl"
