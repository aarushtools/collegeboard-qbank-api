import asyncio
import datetime as dt
import uuid

import httpx
import pytest

import main


LOOKUP_URL = (
    "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank/lookup"
)
QUESTION_URL = "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank/digital/get-question"

pytestmark = pytest.mark.integration


def _get_by_name(items, name):
    return next(item for item in items if item.name == name)


def _post_live_question(external_id: uuid.UUID) -> dict:
    response = httpx.post(
        QUESTION_URL,
        json={"external_id": str(external_id)},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def _find_live_question_of_type(external_ids: set[uuid.UUID], expected_type: str) -> tuple[uuid.UUID, dict]:
    for external_id in external_ids:
        payload = _post_live_question(external_id)
        if payload["type"] == expected_type:
            return external_id, payload
    raise AssertionError(f"Could not find a live {expected_type!r} question in the provided ids")


async def _await_completed_pdf_url(
    manager: main.QBankAssessmentClient._QuestionManager,
    questions,
    style,
    *,
    timeout_seconds: float = 120.0,
    request_speed_interval: float = 1.0,
) -> str:
    async def run():
        generator = manager.create_pdf_url(
            questions, style, request_speed_interval=request_speed_interval
        )
        try:
            async for item in generator:
                if isinstance(item, main.QBankDownloadProgress) and item.download_url:
                    return item.download_url
        finally:
            await generator.aclose()
        raise AssertionError("Did not receive a completed PDF download URL")

    try:
        async with asyncio.timeout(timeout_seconds):
            return await run()
    except TimeoutError as exc:
        raise AssertionError("Timed out waiting for PDF generation to complete") from exc


def test_live_lookup_metadata_contract():
    client = main.QBankMetadataClient(lookup_url=LOOKUP_URL)

    assert any(assessment.name == "SAT" for assessment in client.assessments)
    assert any(module.name == "Math" for module in client.test_modules)
    assert any(module.name == "Reading and Writing" for module in client.test_modules)
    assert len(client.math_live_items) > 0
    assert len(client.reading_live_items) > 0


def test_live_get_question_contract_for_reading_mcq():
    metadata = main.QBankMetadataClient(lookup_url=LOOKUP_URL)
    external_id, payload = _find_live_question_of_type(metadata.reading_live_items, "mcq")

    assert payload["externalid"] == str(external_id)
    assert payload["type"] == "mcq"
    assert isinstance(payload["answerOptions"], list)
    assert len(payload["answerOptions"]) >= 4
    assert isinstance(payload["correct_answer"], list)
    assert isinstance(payload["keys"], list)


def test_live_get_question_contract_for_math_spr():
    metadata = main.QBankMetadataClient(lookup_url=LOOKUP_URL)
    external_id, payload = _find_live_question_of_type(metadata.math_live_items, "spr")

    assert payload["externalid"] == str(external_id)
    assert payload["type"] == "spr"
    assert payload.get("answerOptions") is None
    assert payload.get("stimulus") is None
    assert isinstance(payload["correct_answer"], list)
    assert isinstance(payload["keys"], list)


def test_live_assessment_client_can_list_and_fetch_math_question():
    metadata = main.QBankMetadataClient(lookup_url=LOOKUP_URL)
    assessment = _get_by_name(metadata.assessments, "SAT")
    module = _get_by_name(metadata.test_modules, "Math")
    domain = _get_by_name(module.domains, "Algebra")

    client = main.QBankAssessmentClient(
        assessment,
        module,
        [domain],
        tz=dt.timezone.utc,
    )
    manager = client.QuestionManager()
    available_questions = [question for question in manager.all() if question.external_id]

    assert client.question_count() > 0
    assert len(available_questions) > 0

    question = available_questions[0]
    detailed = manager.fetch(question, max_retries=1)

    assert detailed.question_summary == question
    assert detailed.type in {"mcq", "spr"}
    assert isinstance(detailed.correct_answers, tuple)


def test_live_pdf_generation_contract_for_small_question_set():
    metadata = main.QBankMetadataClient(lookup_url=LOOKUP_URL)
    assessment = _get_by_name(metadata.assessments, "SAT")
    module = _get_by_name(metadata.test_modules, "Math")
    domain = _get_by_name(module.domains, "Algebra")

    client = main.QBankAssessmentClient(
        assessment,
        module,
        [domain],
        tz=dt.timezone.utc,
    )
    manager = client.QuestionManager()
    questions = [q for q in manager.all() if q.external_id][:2]
    if not questions:
        pytest.skip("No live questions with external_id available to generate a PDF")

    download_url = asyncio.run(
        _await_completed_pdf_url(
            manager,
            questions,
            main.QBankPDFStyle.NO_ANSWER_OR_EXPL,
            timeout_seconds=180.0,
            request_speed_interval=1.0,
        )
    )

    response = httpx.get(download_url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    assert response.content.startswith(b"%PDF")
