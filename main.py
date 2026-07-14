from datetime import datetime, timezone
from typing import Literal
import uuid
from zoneinfo import ZoneInfo
import httpx

from schemas import Assessment, Domain, QuestionSummary, Skill, TestModule


class QBankAPIFailure(httpx.HTTPError):
    """All QBank API errors subclass from this class"""

    def __init__(self, message: str, request: httpx.Request):
        super().__init__(message)
        self.request = request


class QBankHTTPStatusError(QBankAPIFailure):
    """Raised when the QBank API returns a 4xx or 5xx status code."""

    def __init__(self, message: str, request: httpx.Request, response: httpx.Response):
        super().__init__(message, request)
        self.response = response


class QBankMetadataClient:
    """A client to fetch question bank initial lookup metadata"""

    DEFAULT_LOOKUP_URL = "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank/lookup"
    MODULE_SHORTHAND_LOOKUP_TABLE = {"Math": "Math", "Reading and Writing": "R&W"}

    def __init__(
        self,
        lookup_url: str = DEFAULT_LOOKUP_URL,
    ) -> None:
        try:
            response = httpx.get(lookup_url, timeout=10.0)
            response.raise_for_status()
            self._raw = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                error_detail = exc.response.json().get(
                    "message"
                ) or exc.response.json().get("detail")
            except ValueError, AttributeError:
                error_detail = exc.response.text or "No error text provided"
            msg = f"QBank API Server Error [{exc.response.status_code}]: {error_detail}"
            raise QBankHTTPStatusError(msg, response=exc.response) from exc
        except httpx.HTTPError as exc:
            raise QBankAPIFailure(f"QBank Network/Transport Error: {exc}") from exc
        except Exception as exc:
            raise QBankAPIFailure(f"Unexpected system error occurred: {exc}") from exc

    @property
    def assessments(self) -> list[Assessment]:
        return [
            Assessment(id=assmnt["id"], name=assmnt["text"])
            for assmnt in self._raw["lookupData"]["assessment"]
        ]

    @property
    def test_modules(self) -> list[TestModule]:
        module_list = []
        for module in self._raw["lookupData"]["test"]:
            module_lookup_name = self.MODULE_SHORTHAND_LOOKUP_TABLE[module["text"]]
            mdl_domains = [
                Domain(
                    id=domain["id"],
                    name=domain["text"],
                    code=domain["primaryClassCd"],
                    skills=[
                        Skill(id=skill["id"], name=skill["text"])
                        for skill in domain["skill"]
                    ],
                )
                for domain in self._raw["lookupData"]["domain"][module_lookup_name]
            ]

            module_list.append(
                TestModule(id=module["id"], name=module["text"], domains=mdl_domains)
            )

    @property
    def math_live_items(self) -> set[uuid.UUID]:
        return set(map(uuid.UUID, self._raw["mathLiveItems"]))

    @property
    def reading_live_items(self) -> set[uuid.UUID]:
        return set(map(uuid.UUID, self._raw["readingLiveItems"]))


class QBankAssessmentClient:
    """A client to fetch the specific questions for an assessment"""

    DEFAULT_QUESTION_URL = "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank/digital/get-questions"
    DEFAULT_TZ = ZoneInfo("America/New_York")

    def __init__(
        self,
        assessment: Assessment,
        module: TestModule,
        domains: list[Domain],
        tz: ZoneInfo | None,
        question_url: str = DEFAULT_QUESTION_URL,
    ) -> None:
        self.assessment = assessment
        self.module = module
        self.domains = domains
        self.tz = tz or self.DEFAULT_TZ

        try:
            response = httpx.post(
                question_url,
                json={
                    "asmtEventId": assessment.id,
                    "test": module.id,
                    "domain": ",".join([d.code for d in domains]),
                },
                timeout=10.0,
            )
            response.raise_for_status()
            self._raw = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                error_detail = exc.response.json().get(
                    "message"
                ) or exc.response.json().get("detail")
            except ValueError, AttributeError:
                error_detail = exc.response.text or "No error text provided"
            msg = f"QBank API Server Error [{exc.response.status_code}]: {error_detail}"
            raise QBankHTTPStatusError(msg, response=exc.response) from exc
        except httpx.HTTPError as exc:
            raise QBankAPIFailure(f"QBank Network/Transport Error: {exc}") from exc
        except Exception as exc:
            raise QBankAPIFailure(f"Unexpected system error occurred: {exc}") from exc

    def question_count(self) -> int:
        return len(self._raw)

    def QuestionManager(self) -> _QuestionManager:
        return self._QuestionManager(self)

    class _QuestionManager:
        def __init__(self, client) -> None:
            self.client = client
            self.questions = []
            DOMAIN_LOOKUP_TABLE = {d_obj.name: d_obj for d_obj in client.domains}
            SKILL_LOOKUP_TABLE = {
                skill.name: skill for d_obj in client.domains for skill in d_obj
            }

            for q in client._raw:
                self.questions.append(
                    assessment=client.assessment,
                    domain=DOMAIN_LOOKUP_TABLE[q["primary_class_cd_desc"]],
                    skill=SKILL_LOOKUP_TABLE[q["skill_desc"]],
                    external_id=uuid.UUID(q["external_id"]),
                    uuid=uuid.UUID(q["uId"]),
                    difficulty=q["difficulty"],
                    score_band=int(q["score_band_range_cd"]),
                    last_updated_date=datetime.fromtimestamp(
                        q["updateDate"] / 1000, tz=client.tz
                    ).date(),
                    created_date=datetime.fromtimestamp(
                        q["createDate"] / 1000, tz=client.tz
                    ).date(),
                )

            self.questions = tuple(self.questions)

        def all(self) -> _QuestionCollection:
            return _QuestionCollection(self.questions)


class _QuestionCollection:
    def __init__(self, questions: tuple[QuestionSummary]):
        self.questions = questions

    def filter_by_skill(self, skill: Skill) -> _QuestionCollection:
        return _QuestionCollection(tuple(q.skill == skill for q in self.questions))

    def filter_by_domain(self, domain: Domain) -> _QuestionCollection:
        return _QuestionCollection(
            tuple(q.domain == domain for q in self.questions)
        )

    def filter_by_difficulty(
        self, difficulty: Literal["E", "M", "H"]
    ) -> _QuestionCollection:
        return _QuestionCollection(
            tuple(q.difficulty == difficulty for q in self.questions)
        )

    def filter_by_score_band(
        self,
        *,
        gt: int | None = None,
        gte: int | None = None,
        lt: int | None = None,
        lte: int | None = None,
        eq: int | None = None,
    ) -> _QuestionCollection:
        qs = self._questions

        if eq is not None:
            qs = [q for q in qs if q.score_band == eq]
        if gt is not None:
            qs = [q for q in qs if q.score_band > gt]
        if gte is not None:
            qs = [q for q in qs if q.score_band >= gte]
        if lt is not None:
            qs = [q for q in qs if q.score_band < lt]
        if lte is not None:
            qs = [q for q in qs if q.score_band <= lte]

        return _QuestionCollection(qs)
