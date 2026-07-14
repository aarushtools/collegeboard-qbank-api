from dataclasses import dataclass, field
import datetime
from enum import StrEnum
from typing import Literal, Optional
import uuid


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
    domains: list[Domain]


@dataclass(frozen=True)
class Skill:
    id: int
    name: str


@dataclass(frozen=True)
class Domain:
    id: int
    name: str
    code: str
    skills: list[Skill]


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
    stimulus: str
    type: Literal["mcq", "spr"]
    rationale: str
    question_summary: QuestionSummary
    answers: list[Answer]
    correct_answers: list[Answer]


@dataclass(frozen=True)
class Answer:
    id: uuid.UUID | None
    content: str


class QBankDownloadProgress:
    status: Literal["COMPLETED", "IN_PROGRESS"]
    download_url: str


@dataclass(frozen=True)
class QBankLiveDownloadResults(QBankDownloadProgress):
    status: Literal["IN_PROGRESS"] = field(default="IN_PROGRESS", init=False)
    questions_processed: int
    total_questions: int
    estimated_time_remaining: datetime.timedelta


class QBankPDFStyle(StrEnum):
    ANSWERS_AND_EXPLANATIONS: str = "with-ans-and-expl"
    NO_ANSWER_NO_HEADER: str = "no-ans-no-hdr"
    NO_ANSWER_OR_EXPL: str = "no-ans-no-expl"
