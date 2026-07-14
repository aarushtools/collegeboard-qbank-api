from dataclasses import dataclass
import datetime
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
    difficulty: Literal["E", "M", "H"]
    score_band: int
    last_updated_date: datetime.date
    created_date: datetime.date
