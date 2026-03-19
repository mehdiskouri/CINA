import pytest

from cina.ingestion.chunking.sentences import split_sentences


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("   ", []),
        ("Single sentence.", ["Single sentence."]),
        ("One. Two.", ["One.", "Two."]),
        ("One? Two! Three.", ["One?", "Two!", "Three."]),
        ("Line one.\n\nLine two.", ["Line one.", "Line two."]),
        ("A.  B.   C.", ["A.", "B.", "C."]),
        ("Dr. Smith arrived. Patient stable.", ["Dr. Smith arrived.", "Patient stable."]),
        (
            "Medication is p.o. twice daily. Continue treatment.",
            ["Medication is p.o. twice daily.", "Continue treatment."],
        ),
        (
            "Infusion i.v. now. Monitor vitals.",
            ["Infusion i.v. now.", "Monitor vitals."],
        ),
        (
            "Dose b.i.d. for 7 days. Reassess after course.",
            ["Dose b.i.d. for 7 days.", "Reassess after course."],
        ),
        (
            "Taken t.i.d. with meals. No adverse events.",
            ["Taken t.i.d. with meals.", "No adverse events."],
        ),
        (
            "Use q.i.d. as needed. Follow-up tomorrow.",
            ["Use q.i.d. as needed.", "Follow-up tomorrow."],
        ),
        (
            "Comparison vs. placebo showed effect. Results replicated.",
            ["Comparison vs. placebo showed effect.", "Results replicated."],
        ),
        (
            "Findings by et al. were confirmed. Additional data pending.",
            ["Findings by et al. were confirmed.", "Additional data pending."],
        ),
        (
            "See Fig. 2 for details. Conclusion follows.",
            ["See Fig. 2 for details.", "Conclusion follows."],
        ),
        (
            "Mr. Doe was discharged. Ms. Roe remained.",
            ["Mr. Doe was discharged.", "Ms. Roe remained."],
        ),
        (
            "Mrs. Lee improved overnight. Plan unchanged.",
            ["Mrs. Lee improved overnight.", "Plan unchanged."],
        ),
        ("No punctuation at end", ["No punctuation at end"]),
        ("Multiple   spaces   inside. Next line.", ["Multiple spaces inside.", "Next line."]),
        ("Trailing punctuation!!!", ["Trailing punctuation!!!"]),
        ("Alpha... Beta.", ["Alpha...", "Beta."]),
        (
            "Dr. A noted fever? Yes. Start antibiotics.",
            ["Dr. A noted fever?", "Yes.", "Start antibiotics."],
        ),
    ],
)
def test_split_sentences_cases(text: str, expected: list[str]) -> None:
    assert split_sentences(text) == expected
