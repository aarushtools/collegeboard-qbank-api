import asyncio
import builtins
import time
import uuid
from datetime import date, datetime, timedelta
from typing import (
    Any,
    AsyncGenerator,
    Iterable,
    Literal,
    NotRequired,
    TypedDict,
)
from zoneinfo import ZoneInfo

import httpx

from schemas import (
    Answer,
    Assessment,
    DetailedQuestion,
    Domain,
    QBankDownloadProgress,
    QBankLiveDownloadResults,
    QBankPDFStyle,
    QuestionSummary,
    Skill,
    TestModule,
)


class AnswerOption(TypedDict):
    id: str
    content: str


class AnswerPayloadBase(TypedDict):
    type: Literal["mcq", "spr"]
    stem: str
    rationale: str
    stimulus: NotRequired[str | None]
    answerOptions: NotRequired[list[AnswerOption] | None]
    keys: list[str]


class QBankAPIFailure(httpx.HTTPError):
    """All QBank API errors subclass from this class."""

    def __init__(self, message: str, request: httpx.Request | None = None):
        super().__init__(message)
        self._request = request

    @property
    def request(self) -> httpx.Request | None:  # type: ignore[override]
        return self._request


class QBankHTTPStatusError(QBankAPIFailure):
    """Raised when the QBank API returns a 4xx or 5xx status code (except for 429, which returns QBankRateLimitedError)."""

    def __init__(
        self,
        message: str,
        request: httpx.Request | None,
        response: httpx.Response,
    ):
        super().__init__(message, request)
        self.response = response


class QBankRateLimitedError(QBankAPIFailure):
    """Raised when the QBank API returns a 429 status code."""

    def __init__(
        self,
        message: str,
        request: httpx.Request | None,
        response: httpx.Response,
        retry_after: str | int | None,
    ):
        super().__init__(message, request)
        self.response = response
        try:
            self.retry_after = int(retry_after) if retry_after is not None else 60
        except TypeError, ValueError:
            self.retry_after = 60


class QBankQuestionCollectionInvalidType(TypeError):
    """Raised when an invalid type (not QuestionSummary or DetailedQuestion) is found in a _QuestionCollection or equivalent Iterable."""


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
            raise QBankHTTPStatusError(
                msg,
                request=exc.request,
                response=exc.response,
            ) from exc
        except httpx.HTTPError as exc:
            raise QBankAPIFailure(
                f"QBank Network/Transport Error: {exc}",
                request=exc.request,
            ) from exc
        except Exception as exc:
            raise QBankAPIFailure(f"Unexpected system error occurred: {exc}") from exc

    @property
    def assessments(self) -> list[Assessment]:
        return [
            Assessment(id=int(assmnt["id"]), name=assmnt["text"])
            for assmnt in self._raw["lookupData"]["assessment"]
        ]

    @property
    def test_modules(self) -> list[TestModule]:
        module_list = []
        for module in self._raw["lookupData"]["test"]:
            module_lookup_name = self.MODULE_SHORTHAND_LOOKUP_TABLE[module["text"]]
            mdl_domains = tuple(
                Domain(
                    id=int(domain["id"]),
                    name=domain["text"],
                    code=domain["primaryClassCd"],
                    skills=tuple(
                        Skill(id=int(skill["id"]), name=skill["text"])
                        for skill in domain["skill"]
                    ),
                )
                for domain in self._raw["lookupData"]["domain"][module_lookup_name]
            )

            module_list.append(
                TestModule(
                    id=int(module["id"]), name=module["text"], domains=mdl_domains
                )
            )

        return module_list

    @property
    def math_live_items(self) -> set[uuid.UUID]:
        return set(map(uuid.UUID, self._raw["mathLiveItems"]))

    @property
    def reading_live_items(self) -> set[uuid.UUID]:
        return set(map(uuid.UUID, self._raw["readingLiveItems"]))


class QBankAssessmentClient:
    """A client to fetch the specific questions for an assessment"""

    DEFAULT_QUESTION_URL = "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank/digital/get-questions"
    DEFAULT_FETCH_URL = "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank/digital/get-question"
    DEFAULT_DOWNLOAD_URL = "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank/pdf-download-v2"
    DEFAULT_TZ = ZoneInfo("America/New_York")

    def __init__(
        self,
        assessment: Assessment,
        module: TestModule,
        domains: tuple[Domain, ...],
        *,
        tz: ZoneInfo | None,
        question_url: str = DEFAULT_QUESTION_URL,
        fetch_url: str = DEFAULT_FETCH_URL,
        download_url: str = DEFAULT_DOWNLOAD_URL,
    ) -> None:
        self.assessment = assessment
        self.module = module
        self.domains = domains
        self.tz = tz or self.DEFAULT_TZ
        self.question_url = question_url
        self.fetch_url = fetch_url
        self.download_url = download_url

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
            raise QBankHTTPStatusError(
                msg,
                request=exc.request,
                response=exc.response,
            ) from exc
        except httpx.HTTPError as exc:
            raise QBankAPIFailure(
                f"QBank Network/Transport Error: {exc}",
                request=exc.request,
            ) from exc
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
                skill.name: skill for d_obj in client.domains for skill in d_obj.skills
            }

            for q in client._raw:
                external_id_raw = q.get("external_id")
                if external_id_raw is None:
                    # Some question types have no external_id (but do have an IBN); skip them so
                    # downstream code can always assume `external_id` exists.
                    continue
                self.questions.append(
                    QuestionSummary(
                        assessment=client.assessment,
                        domain=DOMAIN_LOOKUP_TABLE[q["primary_class_cd_desc"]],
                        skill=SKILL_LOOKUP_TABLE[q["skill_desc"]],
                        external_id=uuid.UUID(external_id_raw),
                        uuid=uuid.UUID(q["uId"]),
                        question_id=q["questionId"],
                        difficulty=q["difficulty"],
                        score_band=int(q["score_band_range_cd"]),
                        last_updated_date=datetime.fromtimestamp(
                            q["updateDate"] / 1000, tz=client.tz
                        ).date(),
                        created_date=datetime.fromtimestamp(
                            q["createDate"] / 1000, tz=client.tz
                        ).date(),
                    )
                )

            self.questions = set(self.questions)

        def all(self) -> _QuestionCollection:
            return _QuestionCollection(self.questions)

        @staticmethod
        def _get_external_id(q: QuestionSummary | uuid.UUID) -> uuid.UUID:
            if isinstance(q, QuestionSummary):
                external_id = q.external_id
            elif isinstance(q, uuid.UUID):
                external_id = q
            else:
                raise QBankQuestionCollectionInvalidType(
                    f"While fetching data, could not find an external question UUID for {type(q)}. Expected QuestionSummary or uuid.UUID. Got {q!r}"
                )

            return external_id

        @classmethod
        def _http_status_error_to_qbank(
            cls, exc: httpx.HTTPStatusError
        ) -> QBankHTTPStatusError | QBankRateLimitedError:
            try:
                error_detail = exc.response.json().get(
                    "message"
                ) or exc.response.json().get("detail")
            except ValueError, AttributeError:
                error_detail = exc.response.text or "No error text provided"
            msg = f"QBank API Server Error [{exc.response.status_code}]: {error_detail}"
            if exc.response.status_code == 429:
                return QBankRateLimitedError(
                    msg,
                    request=exc.request,
                    response=exc.response,
                    retry_after=exc.response.headers.get("Retry-After"),
                )
            return QBankHTTPStatusError(
                msg,
                request=exc.request,
                response=exc.response,
            )

        @staticmethod
        def _process_answer_json(
            raw: "AnswerPayloadBase",
            question_summary: QuestionSummary,
        ) -> DetailedQuestion:
            answer_options = raw.get("answerOptions") or []
            answer_list = {
                answer["id"]: Answer(id=answer["id"], content=answer["content"])
                for answer in answer_options
            }
            correct_answers = []
            for key in raw["keys"]:
                if key in answer_list:
                    correct_answers.append(answer_list[key])
                else:
                    correct_answers.append(Answer(id=None, content=key))

            return DetailedQuestion(
                type=raw["type"],
                stem=raw["stem"],
                stimulus=raw.get("stimulus"),
                rationale=raw["rationale"],
                question_summary=question_summary,
                answers=tuple(answer_list.values()),
                correct_answers=tuple(correct_answers),
            )

        def fetch(
            self,
            question: QuestionSummary | uuid.UUID,
            _client: httpx.Client | None = None,
            *,
            max_retries: int = 3,
        ) -> DetailedQuestion:
            external_id = self._get_external_id(question)
            if isinstance(question, uuid.UUID):
                question_summary = self.all().get_by_external_id(question)
                if not isinstance(question_summary, QuestionSummary):
                    raise QBankAPIFailure(
                        f"Unexpected item in collection for external_id={question!r}"
                    )
            else:
                question_summary = question

            client: Any = _client or httpx
            retries = 0
            while True:
                try:
                    try:
                        response = client.post(
                            self.client.fetch_url,
                            json={"external_id": str(external_id)},
                            timeout=2.0,
                        )
                        response.raise_for_status()
                        raw: Any = response.json()
                    except httpx.HTTPStatusError as exc:
                        raise self._http_status_error_to_qbank(exc) from exc
                    except httpx.HTTPError as exc:
                        raise QBankAPIFailure(
                            f"QBank Network/Transport Error: {exc}",
                            request=exc.request,
                        ) from exc
                    except Exception as exc:
                        raise QBankAPIFailure(
                            f"Unexpected system error occurred: {exc}"
                        ) from exc
                    return self._process_answer_json(raw, question_summary)

                except QBankRateLimitedError as ex:
                    retries += 1
                    if ex.retry_after is None or retries > max_retries:
                        raise
                    time.sleep(float(ex.retry_after))

                except QBankAPIFailure:
                    retries += 1
                    if retries > max_retries:
                        raise

        def fetchmany(
            self,
            question_list: Iterable[QuestionSummary | uuid.UUID],
            *,
            max_retries: int = 3,
        ) -> _QuestionCollection[DetailedQuestion]:
            questions: list[DetailedQuestion] = []

            with httpx.Client() as client:
                for q in question_list:
                    questions.append(self.fetch(q, client, max_retries=max_retries))

            return _QuestionCollection(questions)

        async def afetch(
            self,
            question: QuestionSummary | uuid.UUID,
            client: httpx.AsyncClient,
            *,
            max_retries: int = 3,
        ) -> DetailedQuestion:
            external_id = self._get_external_id(question)
            question_summary = (
                self.all().get_by_external_id(question)
                if type(question) is uuid.UUID
                else question
            )
            if not isinstance(question_summary, QuestionSummary):
                raise QBankAPIFailure(
                    f"Unexpected item in collection for external_id={question!r}"
                )

            retries = 0
            while True:
                try:
                    try:
                        response = await client.post(
                            self.client.fetch_url,
                            json={"external_id": str(external_id)},
                            timeout=2.0,
                        )
                        response.raise_for_status()
                        raw: Any = response.json()
                    except httpx.HTTPStatusError as exc:
                        raise self._http_status_error_to_qbank(exc) from exc
                    except httpx.HTTPError as exc:
                        raise QBankAPIFailure(
                            f"QBank Network/Transport Error: {exc}",
                            request=exc.request,
                        ) from exc
                    except Exception as exc:
                        raise QBankAPIFailure(
                            f"Unexpected system error occurred: {exc}"
                        ) from exc
                    return self._process_answer_json(raw, question_summary)

                except QBankRateLimitedError as ex:
                    retries += 1
                    if ex.retry_after is None or retries > max_retries:
                        raise
                    await asyncio.sleep(float(ex.retry_after))

                except QBankAPIFailure:
                    retries += 1
                    if retries > max_retries:
                        raise

        async def afetchmany(
            self,
            question_list: Iterable[QuestionSummary | uuid.UUID],
            *,
            max_retries: int = 3,
            concurrency: int = 10,
        ) -> _QuestionCollection[DetailedQuestion]:
            sem = asyncio.Semaphore(concurrency)

            async with httpx.AsyncClient() as client:
                tasks = []

                for q in question_list:
                    await sem.acquire()
                    task = asyncio.create_task(
                        self.afetch(q, client, max_retries=max_retries)
                    )
                    task.add_done_callback(lambda _: sem.release())
                    tasks.append(task)

                questions = await asyncio.gather(*tasks)

            return _QuestionCollection(questions)

        PDFYield = QBankDownloadProgress | QBankLiveDownloadResults | dict[str, Any]

        async def create_pdf_url(
            self,
            question_list: Iterable[QuestionSummary | DetailedQuestion],
            style: QBankPDFStyle,
            *,
            request_speed_interval: float = 1.0,
        ) -> AsyncGenerator[PDFYield, None]:
            question_ids = []
            for q in question_list:
                if isinstance(q, DetailedQuestion):
                    q_id = q.question_summary.question_id
                elif isinstance(q, QuestionSummary):
                    q_id = q.question_id
                else:
                    raise QBankQuestionCollectionInvalidType(
                        f"Unexpected type of {type(q)} is not QuestionSummary or DetailedQuestion. Got {q!r}"
                    )
                question_ids.append(q_id)

            async with httpx.AsyncClient() as client:
                try:
                    while True:
                        try:
                            response = await client.post(
                                self.client.download_url,
                                json={
                                    "stateStandardsCode": "default",
                                    "configKey": style.value,
                                    "asmtId": self.client.assessment.id,
                                    "questions": [
                                        {"questionId": q_id} for q_id in question_ids
                                    ],
                                },
                                timeout=2.0,
                            )
                            response.raise_for_status()
                            raw: Any = response.json()
                        except httpx.HTTPStatusError as exc:
                            try:
                                error_detail = exc.response.json().get(
                                    "message"
                                ) or exc.response.json().get("detail")
                            except ValueError, AttributeError:
                                error_detail = (
                                    exc.response.text or "No error text provided"
                                )

                            msg = f"QBank API Server Error [{exc.response.status_code}]: {error_detail}"
                            if exc.response.status_code == 429:
                                raise QBankRateLimitedError(
                                    msg,
                                    request=exc.request,
                                    response=exc.response,
                                    retry_after=exc.response.headers.get("Retry-After"),
                                ) from exc
                            raise QBankHTTPStatusError(
                                msg,
                                request=exc.request,
                                response=exc.response,
                            ) from exc
                        except httpx.HTTPError as exc:
                            raise QBankAPIFailure(
                                f"QBank Network/Transport Error: {exc}",
                                request=exc.request,
                            ) from exc
                        except Exception as exc:
                            raise QBankAPIFailure(
                                f"Unexpected system error occurred: {exc}"
                            ) from exc

                        if raw.get("status") == "FAILED":
                            raise QBankAPIFailure("PDF download failed")

                        elif raw.get("status") == "COMPLETED":
                            yield QBankDownloadProgress(
                                status="COMPLETED", download_url=raw["downloadUrl"]
                            )

                        elif raw.get("status") == "IN_PROGRESS":
                            yield QBankLiveDownloadResults(
                                download_url=None,
                                questions_processed=raw["progress"][
                                    "questionsProcessed"
                                ],
                                total_questions=raw["progress"]["totalQuestions"],
                                estimated_time_remaining=timedelta(
                                    milliseconds=float(
                                        raw["progress"]["estimatedTimeRemainingMs"]
                                    )
                                ),
                            )

                        else:
                            yield raw

                        await asyncio.sleep(request_speed_interval)

                except QBankRateLimitedError as ex:
                    await asyncio.sleep(float(ex.retry_after))
                    async for item in self.create_pdf_url(question_list, style):
                        yield item


class _QuestionCollection:
    def __init__(
        self,
        questions: Iterable[QuestionSummary | DetailedQuestion] | "_QuestionCollection",
    ):
        if isinstance(questions, _QuestionCollection):
            self._questions = questions._questions
        else:
            self._questions = frozenset(questions)

    def __iter__(self):
        return iter(self._questions)

    def __len__(self):
        return len(self._questions)

    def __or__(self, other: "_QuestionCollection") -> "_QuestionCollection":
        return _QuestionCollection(self._questions | other._questions)

    def __and__(self, other: "_QuestionCollection") -> "_QuestionCollection":
        return _QuestionCollection(self._questions & other._questions)

    def __sub__(self, other: "_QuestionCollection") -> "_QuestionCollection":
        return _QuestionCollection(self._questions - other._questions)

    def get_by_external_id(
        self, external_id: uuid.UUID
    ) -> QuestionSummary | DetailedQuestion:
        for q in self._questions:
            if self._sum_obj(q).external_id == external_id:
                return q
        raise KeyError(f"No question found with external_id={external_id!r}")

    def filter_by_skill(self, skill: Skill) -> "_QuestionCollection":
        return _QuestionCollection(
            q for q in self._questions if self._sum_obj(q).skill == skill
        )

    def filter_by_domain(self, domain: Domain) -> "_QuestionCollection":
        return _QuestionCollection(
            q for q in self._questions if self._sum_obj(q).domain == domain
        )

    def filter_by_difficulty(
        self, difficulty: Literal["E", "M", "H"]
    ) -> "_QuestionCollection":
        return _QuestionCollection(
            q for q in self._questions if self._sum_obj(q).difficulty == difficulty
        )

    def filter_by_score_band(
        self,
        *,
        gt: int | None = None,
        gte: int | None = None,
        lt: int | None = None,
        lte: int | None = None,
        eq: int | None = None,
    ) -> "_QuestionCollection":
        qs = self._questions

        if eq is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).score_band == eq)
        if gt is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).score_band > gt)
        if gte is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).score_band >= gte)
        if lt is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).score_band < lt)
        if lte is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).score_band <= lte)

        return _QuestionCollection(qs)

    def filter_by_created_date(
        self,
        *,
        eq: date | None = None,
        gt: date | None = None,
        gte: date | None = None,
        lt: date | None = None,
        lte: date | None = None,
    ) -> "_QuestionCollection":
        qs = self._questions

        if eq is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).created_date == eq)
        if gt is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).created_date > gt)
        if gte is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).created_date >= gte)
        if lt is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).created_date < lt)
        if lte is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).created_date <= lte)

        return _QuestionCollection(qs)

    def filter_by_last_updated_date(
        self,
        *,
        eq: date | None = None,
        gt: date | None = None,
        gte: date | None = None,
        lt: date | None = None,
        lte: date | None = None,
    ) -> "_QuestionCollection":
        qs = self._questions

        if eq is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).last_updated_date == eq)
        if gt is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).last_updated_date > gt)
        if gte is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).last_updated_date >= gte)
        if lt is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).last_updated_date < lt)
        if lte is not None:
            qs = frozenset(q for q in qs if self._sum_obj(q).last_updated_date <= lte)

        return _QuestionCollection(qs)

    def to_list(self) -> list[QuestionSummary | DetailedQuestion]:
        return list(self._questions)

    @property
    def frozenset(self) -> builtins.frozenset[QuestionSummary | DetailedQuestion]:
        return self._questions

    def _sum_obj(self, q: QuestionSummary | DetailedQuestion):
        if type(q) is QuestionSummary:
            return q
        elif type(q) is DetailedQuestion:
            return q.question_summary
        else:
            raise QBankQuestionCollectionInvalidType(
                f"Unexpected type of {type(q)} is not QuestionSummary or DetailedQuestion. Got {q!r}"
            )
