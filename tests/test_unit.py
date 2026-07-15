import asyncio
import datetime as dt
import uuid

import httpx
import pytest

import main
from schemas import (
    Answer,
    Assessment,
    Domain,
    QBankDownloadProgress,
    QBankLiveDownloadResults,
    QBankPDFStyle,
    QuestionSummary,
    Skill,
    TestModule as SchemaTestModule,
)

from tests.test_support import (
    AsyncSequenceClient,
    SyncSequenceClient,
    build_assessment_payload,
    build_assessment_payload_with_null_external_id,
    build_detailed_question,
    build_lookup_payload,
    build_manager_with_summary,
    build_mcq_answer_payload,
    build_mcq_answer_payload_without_stimulus,
    build_question_summary,
    build_schema_objects,
    build_spr_answer_payload,
    collect_from_asyncgen,
    first_from_asyncgen,
    make_request,
    make_response,
    patch_async_client,
)


def test_qbank_error_classes_store_request_response_and_retry_after():
    request = make_request()
    response = make_response(429, headers={"Retry-After": "7"})

    failure = main.QBankAPIFailure("boom", request)
    status_error = main.QBankHTTPStatusError("bad", request, response)
    rate_limited = main.QBankRateLimitedError("slow", request, response, "7")
    default_retry = main.QBankRateLimitedError("slow", request, response, "abc")

    assert failure.request is request
    assert status_error.request is request
    assert status_error.response is response
    assert rate_limited.retry_after == 7
    assert default_retry.retry_after == 60


def test_metadata_client_parses_lookup_payload(monkeypatch):
    monkeypatch.setattr(
        main.httpx,
        "get",
        lambda *args, **kwargs: make_response(200, json_data=build_lookup_payload()),
    )

    client = main.QBankMetadataClient()

    assert [assessment.name for assessment in client.assessments] == [
        "SAT",
        "PSAT/NMSQT & PSAT 10",
    ]
    assert [assessment.id for assessment in client.assessments] == [99, 100]
    assert [module.name for module in client.test_modules] == [
        "Reading and Writing",
        "Math",
    ]
    assert [module.id for module in client.test_modules] == [1, 2]
    assert client.test_modules[1].domains[0].skills[1].name == "Linear functions"
    assert client.math_live_items == {
        uuid.UUID("00272e26-db4e-4b57-b229-9040d402e42f"),
        uuid.UUID("ad73f33e-e2f7-442f-9463-281f925a0111"),
    }
    assert client.reading_live_items == {
        uuid.UUID("002fb221-07c6-4406-a00c-ed57339ea78c")
    }


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (
            make_response(500, json_data={"message": "broken"}),
            r"QBank API Server Error \[500\]",
        ),
        (make_response(502, text="bad gateway"), r"bad gateway"),
    ],
)
def test_metadata_client_http_status_errors(monkeypatch, response, match):
    monkeypatch.setattr(main.httpx, "get", lambda *args, **kwargs: response)

    with pytest.raises(main.QBankHTTPStatusError, match=match):
        main.QBankMetadataClient()


def test_metadata_client_wraps_transport_errors(monkeypatch):
    request = make_request()
    monkeypatch.setattr(
        main.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            httpx.ConnectError("offline", request=request)
        ),
    )

    with pytest.raises(main.QBankAPIFailure, match="Network/Transport Error"):
        main.QBankMetadataClient()


def test_metadata_client_wraps_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        main.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(
        main.QBankAPIFailure, match="Unexpected system error occurred: boom"
    ):
        main.QBankMetadataClient()


def test_assessment_client_parses_questions_and_counts(monkeypatch):
    assessment, domain, module, *_ = build_schema_objects()
    monkeypatch.setattr(
        main.httpx,
        "post",
        lambda *args, **kwargs: make_response(
            200,
            json_data=build_assessment_payload(),
            method="POST",
        ),
    )

    client = main.QBankAssessmentClient(
        assessment,
        module,
        (domain,),
        tz=dt.timezone.utc,
    )
    manager = client.QuestionManager()
    questions = manager.all().to_list()

    assert client.question_count() == 2
    assert client.assessment == assessment
    assert client.module == module
    assert len(questions) == 2
    assert {question.question_id for question in questions} == {"ac472881", "3d1070c9"}


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (
            make_response(503, json_data={"detail": "unavailable"}, method="POST"),
            r"QBank API Server Error \[503\]",
        ),
        (
            make_response(503, text="service unavailable", method="POST"),
            r"service unavailable",
        ),
    ],
)
def test_assessment_client_http_status_errors(monkeypatch, response, match):
    assessment, domain, module, *_ = build_schema_objects()
    monkeypatch.setattr(main.httpx, "post", lambda *args, **kwargs: response)

    with pytest.raises(main.QBankHTTPStatusError, match=match):
        main.QBankAssessmentClient(
            assessment,
            module,
            (domain,),
            tz=dt.timezone.utc,
        )


def test_assessment_client_wraps_transport_errors(monkeypatch):
    assessment, domain, module, *_ = build_schema_objects()
    request = make_request("POST")
    monkeypatch.setattr(
        main.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            httpx.ConnectTimeout("late", request=request)
        ),
    )

    with pytest.raises(main.QBankAPIFailure, match="Network/Transport Error"):
        main.QBankAssessmentClient(
            assessment,
            module,
            (domain,),
            tz=dt.timezone.utc,
        )


def test_assessment_client_wraps_unexpected_errors(monkeypatch):
    assessment, domain, module, *_ = build_schema_objects()
    monkeypatch.setattr(
        main.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(
        main.QBankAPIFailure, match="Unexpected system error occurred: boom"
    ):
        main.QBankAssessmentClient(
            assessment,
            module,
            (domain,),
            tz=dt.timezone.utc,
        )


def test_question_manager_helpers_cover_external_ids_and_answer_processing():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)

    assert (
        main.QBankAssessmentClient._QuestionManager._get_external_id(summary)
        == summary.external_id
    )
    assert (
        main.QBankAssessmentClient._QuestionManager._get_external_id(
            summary.external_id
        )
        == summary.external_id
    )

    with pytest.raises(main.QBankQuestionCollectionInvalidType):
        main.QBankAssessmentClient._QuestionManager._get_external_id("bad")

    detailed = main.QBankAssessmentClient._QuestionManager._process_answer_json(
        build_mcq_answer_payload(),
        summary,
    )

    assert detailed.type == "mcq"
    assert detailed.correct_answers[0].content == "<p>where sound is made.</p>"
    assert detailed.correct_answers[0].id == "4401928d-2800-409b-baf3-475cbaac306e"
    assert list(detailed.answers)[2].content == "<p>where sound is made.</p>"


def test_question_manager_processes_spr_payload_without_answer_options():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)

    detailed = main.QBankAssessmentClient._QuestionManager._process_answer_json(
        build_spr_answer_payload(),
        summary,
    )

    assert detailed.type == "spr"
    assert detailed.stimulus is None
    assert detailed.answers == ()
    assert detailed.correct_answers == (
        Answer(id=None, content="14.5"),
        Answer(id=None, content="29/2"),
    )


def test_question_manager_processes_payload_without_stimulus_key():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)

    detailed = main.QBankAssessmentClient._QuestionManager._process_answer_json(
        build_mcq_answer_payload_without_stimulus(),
        summary,
    )

    assert detailed.stimulus is None


def test_question_manager_accepts_list_backed_domain_skills(monkeypatch):
    assessment = Assessment(id=99, name="SAT")
    skills = [
        Skill(id=1, name="Linear equations in one variable"),
        Skill(id=2, name="Linear functions"),
    ]
    domain = Domain(id=1, name="Algebra", code="H", skills=tuple(skills))
    module = SchemaTestModule(id=2, name="Math", domains=(domain,))
    monkeypatch.setattr(
        main.httpx,
        "post",
        lambda *args, **kwargs: make_response(
            200,
            json_data=build_assessment_payload(),
            method="POST",
        ),
    )

    client = main.QBankAssessmentClient(
        assessment,
        module,
        (domain,),
        tz=dt.timezone.utc,
    )

    manager = client.QuestionManager()

    assert len(manager.all()) == 2
    assert {question.question_id for question in manager.all()} == {
        "ac472881",
        "3d1070c9",
    }


def test_question_manager_skips_null_external_ids(monkeypatch):
    assessment, domain, module, *_ = build_schema_objects()
    monkeypatch.setattr(
        main.httpx,
        "post",
        lambda *args, **kwargs: make_response(
            200,
            json_data=build_assessment_payload_with_null_external_id(),
            method="POST",
        ),
    )

    client = main.QBankAssessmentClient(
        assessment,
        module,
        [domain],
        tz=dt.timezone.utc,
    )

    manager = client.QuestionManager()
    questions = manager.all().to_list()

    assert len(questions) == 2
    assert {q.question_id for q in questions} == {"ac472881", "3d1070c9"}


def test_fetch_returns_detailed_question_for_uuid_lookup():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    client = SyncSequenceClient(
        [make_response(200, json_data=build_mcq_answer_payload(), method="POST")]
    )

    detailed = manager.fetch(summary.external_id, client)

    assert detailed.question_summary == summary
    assert "Which choice completes" in detailed.stem
    assert client.request_payloads[0]["json"]["external_id"] == str(summary.external_id)


def test_fetch_uses_httpx_module_when_client_is_omitted(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return make_response(200, json_data=build_mcq_answer_payload(), method="POST")

    monkeypatch.setattr(main.httpx, "post", fake_post)

    detailed = manager.fetch(summary)

    assert detailed.question_summary == summary
    assert calls[0]["json"]["external_id"] == str(summary.external_id)


def test_fetch_retries_transport_errors_then_succeeds():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    request = make_request("POST")
    client = SyncSequenceClient(
        [
            httpx.ReadTimeout("slow", request=request),
            make_response(200, json_data=build_mcq_answer_payload(), method="POST"),
        ]
    )

    detailed = manager.fetch(summary, client, max_retries=2)

    assert detailed.question_summary == summary
    assert client.calls == 2


def test_fetch_retries_rate_limited_errors_then_succeeds(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    sleep_calls = []
    client = SyncSequenceClient(
        [
            make_response(
                429,
                json_data={"message": "slow down"},
                method="POST",
                headers={"Retry-After": "0"},
            ),
            make_response(200, json_data=build_mcq_answer_payload(), method="POST"),
        ]
    )

    monkeypatch.setattr(main.time, "sleep", lambda delay: sleep_calls.append(delay))

    detailed = manager.fetch(summary, client, max_retries=2)

    assert detailed.question_summary == summary
    assert sleep_calls == [0.0]


def test_fetch_raises_http_status_error_with_text_fallback():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    client = SyncSequenceClient(
        [make_response(500, text="question fetch failed", method="POST")]
    )

    with pytest.raises(main.QBankHTTPStatusError, match="question fetch failed"):
        manager.fetch(summary, client, max_retries=0)


def test_fetch_rate_limit_raises_after_retry_budget_is_exhausted():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    client = SyncSequenceClient(
        [
            make_response(
                429,
                json_data={"message": "slow down"},
                method="POST",
                headers={"Retry-After": "0"},
            )
        ]
    )

    with pytest.raises(main.QBankRateLimitedError, match="slow down"):
        manager.fetch(summary, client, max_retries=0)


def test_fetch_raises_after_retry_budget_is_exhausted():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    request = make_request("POST")
    client = SyncSequenceClient(
        [
            httpx.ConnectError("offline", request=request),
            httpx.ConnectError("still offline", request=request),
        ]
    )

    with pytest.raises(main.QBankAPIFailure, match="Network/Transport Error"):
        manager.fetch(summary, client, max_retries=1)


def test_fetch_wraps_unexpected_errors_after_retries():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)

    class BadResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise RuntimeError("bad payload")

    client = SyncSequenceClient([BadResponse()])

    with pytest.raises(
        main.QBankAPIFailure, match="Unexpected system error occurred: bad payload"
    ):
        manager.fetch(summary, client, max_retries=0)


def test_fetchmany_collects_items(monkeypatch):
    assessment, domain, _, skill_a, skill_b = build_schema_objects()
    summary_a = build_question_summary(
        skill_a, domain, assessment, suffix="a", question_id="Q1"
    )
    summary_b = build_question_summary(
        skill_b, domain, assessment, suffix="c", question_id="Q2"
    )
    expected_a = build_detailed_question(summary_a)
    expected_b = build_detailed_question(summary_b)

    manager = object.__new__(main.QBankAssessmentClient._QuestionManager)
    manager.fetch = lambda question, _client, max_retries=3: (
        expected_a if question == summary_a else expected_b
    )

    class DummyClient:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main.httpx, "Client", DummyClient)

    collection = manager.fetchmany([summary_a, summary_b])

    assert len(collection) == 2
    assert collection.frozenset == frozenset({expected_a, expected_b})


def test_afetch_retries_then_succeeds():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    request = make_request("POST")
    client = AsyncSequenceClient(
        [
            httpx.ConnectTimeout("late", request=request),
            make_response(200, json_data=build_mcq_answer_payload(), method="POST"),
        ]
    )

    detailed = asyncio.run(manager.afetch(summary.external_id, client, max_retries=2))

    assert detailed.question_summary == summary
    assert client.calls == 2
    assert client.request_payloads[1]["json"]["external_id"] == str(summary.external_id)


def test_afetch_retries_rate_limited_errors_then_succeeds(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    sleep_calls = []
    client = AsyncSequenceClient(
        [
            make_response(
                429,
                json_data={"detail": "too many"},
                method="POST",
                headers={"Retry-After": "0"},
            ),
            make_response(200, json_data=build_mcq_answer_payload(), method="POST"),
        ]
    )

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    detailed = asyncio.run(manager.afetch(summary, client, max_retries=2))

    assert detailed.question_summary == summary
    assert sleep_calls == [0.0]


def test_afetch_raises_http_status_error_with_text_fallback():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    client = AsyncSequenceClient(
        [make_response(500, text="async question failed", method="POST")]
    )

    with pytest.raises(main.QBankHTTPStatusError, match="async question failed"):
        asyncio.run(manager.afetch(summary, client, max_retries=0))


def test_afetch_wraps_unexpected_errors_after_retries():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)

    class BadResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise RuntimeError("async bad payload")

    client = AsyncSequenceClient([BadResponse()])

    with pytest.raises(
        main.QBankAPIFailure,
        match="Unexpected system error occurred: async bad payload",
    ):
        asyncio.run(manager.afetch(summary, client, max_retries=0))


def test_afetch_rate_limit_raises_after_retry_budget_is_exhausted():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    client = AsyncSequenceClient(
        [
            make_response(
                429,
                json_data={"detail": "too many"},
                method="POST",
                headers={"Retry-After": "0"},
            )
        ]
    )

    with pytest.raises(main.QBankRateLimitedError, match="too many"):
        asyncio.run(manager.afetch(summary, client, max_retries=0))


def test_afetch_raises_after_retry_budget_is_exhausted():
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    request = make_request("POST")
    client = AsyncSequenceClient(
        [
            httpx.ConnectError("offline", request=request),
            httpx.ConnectError("still offline", request=request),
        ]
    )

    with pytest.raises(main.QBankAPIFailure, match="Network/Transport Error"):
        asyncio.run(manager.afetch(summary, client, max_retries=1))


def test_afetchmany_collects_all_results(monkeypatch):
    assessment, domain, _, skill_a, skill_b = build_schema_objects()
    summary_a = build_question_summary(
        skill_a, domain, assessment, suffix="a", question_id="Q1"
    )
    summary_b = build_question_summary(
        skill_b, domain, assessment, suffix="c", question_id="Q2"
    )
    expected = {
        summary_a: build_detailed_question(summary_a),
        summary_b: build_detailed_question(summary_b),
    }

    manager = object.__new__(main.QBankAssessmentClient._QuestionManager)

    async def fake_afetch(question, client, max_retries=3):
        await asyncio.sleep(0)
        return expected[question]

    manager.afetch = fake_afetch
    patch_async_client(monkeypatch, [])
    collection = asyncio.run(manager.afetchmany([summary_a, summary_b], concurrency=1))

    assert collection.frozenset == frozenset(expected.values())


def test_create_pdf_url_rejects_invalid_question_types():
    manager = object.__new__(main.QBankAssessmentClient._QuestionManager)

    with pytest.raises(main.QBankQuestionCollectionInvalidType):
        asyncio.run(
            first_from_asyncgen(
                manager.create_pdf_url(["bad"], QBankPDFStyle.NO_ANSWER_OR_EXPL)
            )
        )


def test_create_pdf_url_failed_status_raises_api_failure(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)

    patch_async_client(
        monkeypatch,
        [make_response(200, json_data={"status": "FAILED"}, method="POST")],
    )

    with pytest.raises(main.QBankAPIFailure, match="PDF download failed"):
        asyncio.run(
            first_from_asyncgen(
                manager.create_pdf_url([summary], QBankPDFStyle.NO_ANSWER_OR_EXPL)
            )
        )


def test_create_pdf_url_completed_status_yields_download_progress(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)

    patch_async_client(
        monkeypatch,
        [
            make_response(
                200,
                json_data={
                    "status": "COMPLETED",
                    "downloadUrl": "https://example.test/file.pdf",
                },
                method="POST",
            )
        ],
    )
    progress = asyncio.run(
        first_from_asyncgen(
            manager.create_pdf_url([summary], QBankPDFStyle.NO_ANSWER_OR_EXPL)
        )
    )

    assert isinstance(progress, QBankDownloadProgress)
    assert progress.status == "COMPLETED"
    assert progress.download_url.endswith(".pdf")


def test_create_pdf_url_in_progress_yields_progress(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)

    payload = {
        "status": "IN_PROGRESS",
        "progress": {
            "questionsProcessed": 2,
            "totalQuestions": 5,
            "estimatedTimeRemainingMs": 1500,
        },
    }
    patch_async_client(
        monkeypatch, [make_response(200, json_data=payload, method="POST")]
    )
    progress = asyncio.run(
        first_from_asyncgen(
            manager.create_pdf_url(
                [summary],
                QBankPDFStyle.ANSWERS_AND_EXPLANATIONS,
            )
        )
    )

    assert isinstance(progress, QBankLiveDownloadResults)
    assert progress.download_url is None
    assert progress.questions_processed == 2
    assert progress.total_questions == 5
    assert progress.estimated_time_remaining == dt.timedelta(milliseconds=1500)


def test_create_pdf_url_unknown_status_yields_raw_payload(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    detailed = build_detailed_question(summary)
    payload = {"status": "QUEUED", "jobId": "abc123"}

    patch_async_client(
        monkeypatch, [make_response(200, json_data=payload, method="POST")]
    )
    assert (
        asyncio.run(
            first_from_asyncgen(
                manager.create_pdf_url([detailed], QBankPDFStyle.NO_ANSWER_NO_HEADER)
            )
        )
        == payload
    )


def test_create_pdf_url_polls_between_in_progress_updates(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    sleep_calls = []
    responses = [
        make_response(
            200,
            json_data={
                "status": "IN_PROGRESS",
                "progress": {
                    "questionsProcessed": 1,
                    "totalQuestions": 2,
                    "estimatedTimeRemainingMs": 1000,
                },
            },
            method="POST",
        ),
        make_response(
            200,
            json_data={
                "status": "COMPLETED",
                "downloadUrl": "https://example.test/file.pdf",
            },
            method="POST",
        ),
    ]

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    patch_async_client(monkeypatch, responses)
    first, second = asyncio.run(
        collect_from_asyncgen(
            manager.create_pdf_url(
                [summary],
                QBankPDFStyle.NO_ANSWER_OR_EXPL,
                request_speed_interval=0.25,
            ),
            2,
        )
    )

    assert isinstance(first, QBankLiveDownloadResults)
    assert isinstance(second, QBankDownloadProgress)
    assert sleep_calls == [0.25]


def test_create_pdf_url_retries_rate_limited_errors_then_succeeds(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    sleep_calls = []

    responses = [
        make_response(
            429,
            json_data={"message": "slow down"},
            method="POST",
            headers={"Retry-After": "0"},
        ),
        make_response(
            200,
            json_data={
                "status": "COMPLETED",
                "downloadUrl": "https://example.test/file.pdf",
            },
            method="POST",
        ),
    ]

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    patch_async_client(monkeypatch, responses)
    progress = asyncio.run(
        first_from_asyncgen(
            manager.create_pdf_url([summary], QBankPDFStyle.NO_ANSWER_OR_EXPL)
        )
    )

    assert isinstance(progress, QBankDownloadProgress)
    assert progress.download_url.endswith(".pdf")
    assert sleep_calls == [0.0]


def test_create_pdf_url_http_status_error_falls_back_to_text(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)

    patch_async_client(
        monkeypatch,
        [make_response(500, text="pdf gateway failure", method="POST")],
    )

    with pytest.raises(main.QBankHTTPStatusError, match="pdf gateway failure"):
        asyncio.run(
            first_from_asyncgen(
                manager.create_pdf_url([summary], QBankPDFStyle.NO_ANSWER_OR_EXPL)
            )
        )


def test_create_pdf_url_wraps_transport_errors(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)
    request = make_request("POST")

    patch_async_client(
        monkeypatch,
        [httpx.ConnectError("pdf offline", request=request)],
    )

    with pytest.raises(
        main.QBankAPIFailure, match="QBank Network/Transport Error: pdf offline"
    ):
        asyncio.run(
            first_from_asyncgen(
                manager.create_pdf_url([summary], QBankPDFStyle.NO_ANSWER_OR_EXPL)
            )
        )


def test_create_pdf_url_wraps_unexpected_errors(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)

    class BadResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise RuntimeError("pdf bad payload")

    patch_async_client(monkeypatch, [BadResponse()])

    with pytest.raises(
        main.QBankAPIFailure, match="Unexpected system error occurred: pdf bad payload"
    ):
        asyncio.run(
            first_from_asyncgen(
                manager.create_pdf_url([summary], QBankPDFStyle.NO_ANSWER_OR_EXPL)
            )
        )


def test_create_pdf_url_raises_http_status_error_for_non_429(monkeypatch):
    assessment, domain, _, skill_a, _ = build_schema_objects()
    summary = build_question_summary(skill_a, domain, assessment)
    manager = build_manager_with_summary(summary)

    patch_async_client(
        monkeypatch,
        [make_response(500, json_data={"detail": "server error"}, method="POST")],
    )

    with pytest.raises(
        main.QBankHTTPStatusError, match="QBank API Server Error \\[500\\]"
    ):
        asyncio.run(
            first_from_asyncgen(
                manager.create_pdf_url([summary], QBankPDFStyle.NO_ANSWER_OR_EXPL)
            )
        )


def test_question_collection_supports_set_ops_and_filters():
    assessment, domain, _, skill_a, skill_b = build_schema_objects()
    summary_a = build_question_summary(
        skill_a, domain, assessment, suffix="a", question_id="Q1"
    )
    summary_b = QuestionSummary(
        assessment=assessment,
        domain=domain,
        skill=skill_b,
        external_id=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        uuid=uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        question_id="Q2",
        difficulty="H",
        score_band=5,
        last_updated_date=dt.date(2024, 2, 1),
        created_date=dt.date(2023, 11, 1),
    )
    detailed_b = build_detailed_question(summary_b)

    collection_a = main._QuestionCollection([summary_a, detailed_b])
    collection_b = main._QuestionCollection([summary_b])
    collection_copy = main._QuestionCollection(collection_a)

    assert len(collection_copy) == 2
    assert collection_a.get_by_external_id(summary_a.external_id) == summary_a

    with pytest.raises(KeyError):
        collection_a.get_by_external_id(uuid.uuid4())

    assert len(collection_a.filter_by_skill(skill_a)) == 1
    assert len(collection_a.filter_by_domain(domain)) == 2
    assert len(collection_a.filter_by_difficulty("H")) == 1
    assert len(collection_a.filter_by_score_band(gt=2, lt=5)) == 1
    assert len(collection_a.filter_by_score_band(gte=5, lte=5, eq=5)) == 1
    assert len(collection_a.filter_by_created_date(gt=dt.date(2023, 11, 30))) == 1
    assert len(collection_a.filter_by_created_date(eq=dt.date(2023, 11, 1))) == 1
    assert len(collection_a.filter_by_created_date(gte=dt.date(2023, 12, 20))) == 1
    assert len(collection_a.filter_by_created_date(lt=dt.date(2023, 12, 1))) == 1
    assert len(collection_a.filter_by_created_date(lte=dt.date(2023, 11, 1))) == 1
    assert len(collection_a.filter_by_last_updated_date(eq=dt.date(2024, 2, 1))) == 1
    assert len(collection_a.filter_by_last_updated_date(gt=dt.date(2024, 1, 25))) == 1
    assert len(collection_a.filter_by_last_updated_date(lt=dt.date(2024, 1, 25))) == 1
    assert len(collection_a.filter_by_last_updated_date(gte=dt.date(2024, 2, 1))) == 1
    assert len(collection_a.filter_by_last_updated_date(lte=dt.date(2024, 1, 20))) == 1
    assert (collection_a | collection_b).frozenset == frozenset(
        {summary_a, detailed_b, summary_b}
    )
    assert len(collection_a & collection_b) == 0
    assert (collection_a - collection_b).frozenset == collection_a.frozenset
    assert set(collection_a.to_list()) == set(iter(collection_a))


def test_question_collection_rejects_invalid_member_types():
    collection = main._QuestionCollection(["bad"])

    with pytest.raises(main.QBankQuestionCollectionInvalidType):
        collection.filter_by_score_band(eq=1)
