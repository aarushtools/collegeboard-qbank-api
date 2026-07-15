import datetime as dt
import json
import uuid

import httpx

import main
from schemas import (
    Answer,
    Assessment,
    DetailedQuestion,
    Domain,
    QuestionSummary,
    Skill,
    TestModule as SchemaTestModule,
)


def make_request(method: str = "GET", url: str = "https://example.test/api") -> httpx.Request:
    return httpx.Request(method, url)


def make_response(
    status_code: int = 200,
    *,
    json_data=None,
    text: str = "",
    method: str = "GET",
    url: str = "https://example.test/api",
    headers=None,
) -> httpx.Response:
    content = json.dumps(json_data) if json_data is not None else text
    headers = dict(headers or {})
    if json_data is not None:
        headers.setdefault("Content-Type", "application/json")

    return httpx.Response(
        status_code,
        request=make_request(method, url),
        content=content,
        headers=headers,
    )


def build_lookup_payload() -> dict:
    return {
        "lookupData": {
            "assessment": [
                {"id": "99", "text": "SAT"},
                {"id": "100", "text": "PSAT/NMSQT & PSAT 10"},
            ],
            "test": [
                {"id": "1", "text": "Reading and Writing"},
                {"id": "2", "text": "Math"},
            ],
            "domain": {
                "R&W": [
                    {
                        "id": "2",
                        "text": "Craft and Structure",
                        "primaryClassCd": "CAS",
                        "skill": [
                            {"id": "5", "text": "Words in Context"},
                        ],
                    }
                ],
                "Math": [
                    {
                        "id": "1",
                        "text": "Algebra",
                        "primaryClassCd": "H",
                        "skill": [
                            {"id": "1", "text": "Linear equations in one variable"},
                            {"id": "2", "text": "Linear functions"},
                        ],
                    }
                ],
            },
        },
        "mathLiveItems": [
            "00272e26-db4e-4b57-b229-9040d402e42f",
            "ad73f33e-e2f7-442f-9463-281f925a0111",
        ],
        "readingLiveItems": ["002fb221-07c6-4406-a00c-ed57339ea78c"],
        "stateOfferings": [{"name": "Alabama", "stateCd": "AL"}],
    }


def build_assessment_payload() -> list[dict]:
    return [
        {
            "updateDate": 1755117017722,
            "pPcc": "SAT#H",
            "questionId": "ac472881",
            "skill_cd": "H.A.",
            "score_band_range_cd": 7,
            "skill_desc": "Linear equations in one variable",
            "createDate": 1755117017722,
            "program": "SAT",
            "primary_class_cd_desc": "Algebra",
            "ibn": "",
            "external_id": "ad6cdfd4-4643-45b2-b72d-0602fb30ce1c",
            "primary_class_cd": "H",
            "uId": "001e3cd5-5928-4676-b497-48a04ed66b44",
            "difficulty": "H",
        },
        {
            "updateDate": 1743430554988,
            "pPcc": "SAT#H",
            "questionId": "3d1070c9",
            "skill_cd": "H.B.",
            "score_band_range_cd": 2,
            "skill_desc": "Linear functions",
            "createDate": 1743430554988,
            "program": "SAT",
            "primary_class_cd_desc": "Algebra",
            "ibn": "",
            "external_id": "f1332d3b-a308-4640-95e0-cae936826d8d",
            "primary_class_cd": "H",
            "uId": "01277323-dcdb-4fcd-a2ce-89b751ea55ee",
            "difficulty": "E",
        },
    ]


def build_assessment_payload_with_null_external_id() -> list[dict]:
    payload = build_assessment_payload()
    payload.append(
        {
            "updateDate": 1691007959617,
            "pPcc": "SAT#H",
            "questionId": "f224df07",
            "skill_cd": "H.E.",
            "score_band_range_cd": 4,
            "skill_desc": "Linear functions",
            "createDate": 1691007959617,
            "program": "SAT",
            "primary_class_cd_desc": "Algebra",
            "ibn": "022222-DC",
            "external_id": None,
            "primary_class_cd": "H",
            "uId": "016d3534-2566-4551-af72-a61ad0c95b5f",
            "difficulty": "M",
        }
    )
    return payload


def build_mcq_answer_payload() -> dict:
    return {
        "type": "mcq",
        "stem": "<p>Which choice completes the text so that it conforms to the conventions of Standard English?</p>",
        "stimulus": "<p>The reed of a wind instrument is the mouthpiece ______.</p>",
        "rationale": "<p>Choice C is the best answer.</p>",
        "answerOptions": [
            {
                "id": "ded3cc27-dd60-44e1-912e-1b49d9f42258",
                "content": "<p>where sound is made?</p>",
            },
            {
                "id": "78968170-65e2-4474-9c42-d244e5fc0aa2",
                "content": "<p>where is sound made.</p>",
            },
            {
                "id": "4401928d-2800-409b-baf3-475cbaac306e",
                "content": "<p>where sound is made.</p>",
            },
            {
                "id": "2bee7168-0da1-4daf-8bfc-ef1cc5b1cfde",
                "content": "<p>where is sound made?</p>",
            },
        ],
        "correct_answer": ["C"],
        "keys": ["4401928d-2800-409b-baf3-475cbaac306e"],
    }


def build_spr_answer_payload() -> dict:
    return {
        "type": "spr",
        "stem": "<p>In the given system of equations, what is the value of <math><mi>a</mi></math>?</p>",
        "stimulus": None,
        "rationale": "<p>The correct answer is 29/2.</p>",
        "answerOptions": None,
        "correct_answer": ["14.5", "29/2"],
        "keys": ["14.5", "29/2"],
    }


def build_mcq_answer_payload_without_stimulus() -> dict:
    payload = build_mcq_answer_payload()
    payload.pop("stimulus")
    return payload


def build_schema_objects():
    assessment = Assessment(id=99, name="SAT")
    skill_a = Skill(id=1, name="Linear equations in one variable")
    skill_b = Skill(id=2, name="Linear functions")
    domain = Domain(
        id=1,
        name="Algebra",
        code="H",
        skills=(skill_a, skill_b),
    )
    module = SchemaTestModule(id=2, name="Math", domains=(domain,))
    return assessment, domain, module, skill_a, skill_b


def build_question_summary(
    skill: Skill,
    domain: Domain,
    assessment: Assessment,
    *,
    suffix: str = "a",
    question_id: str = "Q1",
) -> QuestionSummary:
    next_suffix = chr(ord(suffix) + 1)
    return QuestionSummary(
        assessment=assessment,
        domain=domain,
        skill=skill,
        external_id=uuid.UUID(
            f"{suffix * 8}-{suffix * 4}-{suffix * 4}-{suffix * 4}-{suffix * 12}"
        ),
        uuid=uuid.UUID(
            f"{next_suffix * 8}-{next_suffix * 4}-{next_suffix * 4}-{next_suffix * 4}-{next_suffix * 12}"
        ),
        question_id=question_id,
        difficulty="M",
        score_band=3,
        last_updated_date=dt.date(2024, 1, 20),
        created_date=dt.date(2023, 12, 20),
    )


def build_detailed_question(summary: QuestionSummary) -> DetailedQuestion:
    return DetailedQuestion(
        type="mcq",
        stem="Stem",
        stimulus="Stimulus",
        rationale="Rationale",
        question_summary=summary,
        answers=(Answer(id=uuid.uuid4(), content="A"),),
        correct_answers=(Answer(id=None, content="B"),),
    )


def build_manager_with_summary(summary: QuestionSummary):
    manager = object.__new__(main.QBankAssessmentClient._QuestionManager)
    manager.client = type(
        "Client",
        (),
        {
            "question_url": "https://example.test/question",
            "fetch_url": "https://example.test/fetch-question",
            "download_url": "https://example.test/pdf",
            "assessment": summary.assessment,
        },
    )()
    manager.questions = {summary}
    manager.all = lambda: main._QuestionCollection(manager.questions)
    return manager


class SyncSequenceClient:
    def __init__(self, events):
        self._events = list(events)
        self.calls = 0
        self.request_payloads = []

    def post(self, *args, **kwargs):
        self.request_payloads.append(kwargs)
        event = self._events[self.calls]
        self.calls += 1
        if isinstance(event, Exception):
            raise event
        return event


class AsyncSequenceClient:
    def __init__(self, events):
        self._events = list(events)
        self.calls = 0
        self.request_payloads = []

    async def post(self, *args, **kwargs):
        self.request_payloads.append(kwargs)
        event = self._events[self.calls]
        self.calls += 1
        if isinstance(event, Exception):
            raise event
        return event


class AsyncClientFactory:
    def __init__(self, events):
        self.client = AsyncSequenceClient(events)

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def first_from_asyncgen(asyncgen):
    try:
        return await anext(asyncgen)
    finally:
        await asyncgen.aclose()


async def collect_from_asyncgen(asyncgen, n: int):
    items = []
    try:
        for _ in range(n):
            items.append(await anext(asyncgen))
        return items
    finally:
        await asyncgen.aclose()


def patch_async_client(monkeypatch, events):
    factory = AsyncClientFactory(events)
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda: factory)
    return factory
