import pytest

from app.models import QuestionCategory, Speaker, TranscriptEvent
from app.questions.dedup import QuestionDeduplicator, similarity
from app.questions.detector import QuestionDetector, classify, is_question, split_sentences, strip_fillers


@pytest.mark.parametrize(
    "text",
    [
        "Can you tell me about yourself?",
        "So, tell me about a time you handled a conflict in your team.",
        "Walk me through your most recent project",
        "How would you optimize a slow SQL query",
        "What is the difference between a list and a tuple in Python?",
        "Explain how useEffect works in React.",
        "Okay great, why do you want to work here?",
        "Describe a situation where you missed a deadline.",
    ],
)
def test_detects_questions(text):
    assert is_question(text)


@pytest.mark.parametrize(
    "text",
    [
        "Thanks for joining today.",
        "I worked at Acme for two years.",
        "Okay?",
        "What I mean is that we use Python.",
        "Great, that sounds good.",
    ],
)
def test_ignores_non_questions(text):
    assert not is_question(text)


def test_strip_fillers_and_split():
    assert strip_fillers("So, um, okay what is REST?") == "what is REST?"
    assert split_sentences("I see you used Python. How did you test it? Great.") == [
        "I see you used Python.",
        "How did you test it?",
        "Great.",
    ]


@pytest.mark.parametrize(
    "text,category",
    [
        ("Tell me about yourself", QuestionCategory.INTRO),
        ("Tell me about a time you disagreed with your manager", QuestionCategory.BEHAVIORAL),
        ("Write a query to find the second highest salary", QuestionCategory.SQL),
        ("What is a Python decorator?", QuestionCategory.PYTHON),
        ("How does useState work in React?", QuestionCategory.REACT_API),
        ("What is your greatest weakness?", QuestionCategory.CAREER),
        ("What would you do if production went down at night?", QuestionCategory.SCENARIO),
        ("Walk me through the architecture of the project you built", QuestionCategory.PROJECT),
        ("How would you build a KPI dashboard in Power BI?", QuestionCategory.DATA_ANALYST),
        ("Explain the difference between processes and threads", QuestionCategory.TECHNICAL),
    ],
)
def test_classify(text, category):
    assert classify(text) == category


def test_classify_uses_word_boundaries_and_question_first():
    """Regression from the live hardware test: 'Thanks for joining' must not make it SQL."""
    det = QuestionDetector()
    found = det.process(_final("Thanks for joining. Walk me through the most challenging project on your resume."))
    assert found[0].category == QuestionCategory.PROJECT
    assert classify("How do you handle joins across three tables?") == QuestionCategory.SQL
    assert classify("What motivates you?") == QuestionCategory.CAREER  # stem match
    # Vague question: the lead-in decides
    vague = QuestionDetector().process(_final("I see you used SQL Server. How did that go?"))
    assert vague[0].category == QuestionCategory.SQL


def test_similarity_and_dedup():
    assert similarity("Tell me about yourself?", "So tell me about yourself.") > 0.82
    assert similarity("What is a Python decorator?", "How do you join two SQL tables?") < 0.5
    d = QuestionDeduplicator(threshold=0.82, window_s=60)
    assert d.check_and_remember("Can you tell me about yourself?", now=0)
    assert not d.check_and_remember("Could you tell me about yourself", now=5)
    assert d.check_and_remember("Why do you want this job?", now=6)
    assert d.check_and_remember("Can you tell me about yourself?", now=100)  # window expired


def _final(text, speaker=Speaker.INTERVIEWER, ts=1000.0):
    return TranscriptEvent(speaker, text, True, "u1", timestamp=ts)


def test_detector_pipeline_dedup_and_context():
    det = QuestionDetector()
    found = det.process(_final("I see you built a recommender system. How did you evaluate it?"))
    assert len(found) == 1
    q = found[0]
    assert q.text == "How did you evaluate it?"
    assert "recommender" in q.context
    assert q.category == QuestionCategory.PROJECT  # classified with lead-in context
    assert det.process(_final("How did you evaluate it?", ts=1010)) == []  # duplicate suppressed


def test_detector_ignores_interim_and_unselected_speakers():
    det = QuestionDetector()
    assert det.process(TranscriptEvent(Speaker.INTERVIEWER, "What is SQL?", False, "u")) == []
    assert det.process(_final("What is a primary key in SQL?", speaker=Speaker.CANDIDATE)) == []
    det2 = QuestionDetector(speakers=frozenset({Speaker.CANDIDATE}))
    assert len(det2.process(_final("What is a primary key in SQL?", speaker=Speaker.CANDIDATE))) == 1


def test_detector_multiple_questions_in_one_utterance():
    det = QuestionDetector()
    found = det.process(_final("What is your strongest skill? And why do you want to join our team?"))
    assert [q.text for q in found] == ["What is your strongest skill?", "Why do you want to join our team?"]
