"""Keyword fallback router (used when the planner LLM is unavailable) and slot-choice matching."""

import pytest

from app.graph.nodes import keyword_route
from app.graph.slot_choice import match_slot


@pytest.mark.parametrize(
    ("question", "specialist"),
    [
        ("What are ICU visiting hours?", "knowledge"),
        ("How do I schedule an MRI scan?", "knowledge"),
        ("What are my upcoming appointments?", "appointments"),
        ("Reschedule my cardiology appointment", "appointments"),
        ("Am I covered for knee replacement under my insurance plan?", "insurance"),
        ("What is my latest blood sugar reading?", "labs"),
        ("My doctor recommended surgery. What pre-operative tests are still pending?", "care_coordination"),
        ("Can I proceed with surgery next week?", "care_coordination"),
        ("Can ibuprofen interact with my medicines?", "medication"),
        ("How much will my knee replacement cost?", "billing"),
    ],
)
def test_keyword_route(question, specialist):
    assert keyword_route(question).tasks[0].specialist == specialist


def test_keyword_route_extracts_arguments():
    task = keyword_route("Reschedule my cardiology appointment").tasks[0]
    assert (task.action, task.department) == ("reschedule", "cardiology")
    assert keyword_route("Is my knee replacement covered?").tasks[0].procedure == "knee replacement"


def test_general_insurance_terms_are_policy_questions():
    assert keyword_route("How long is an approved pre-authorisation valid?").tasks[0].specialist == "knowledge"


def test_slot_choice_continues_a_reschedule():
    history = [
        {"speaker": "user", "content": "Reschedule my cardiology appointment"},
        {"speaker": "assistant", "content": "Which of these available times would you like?"},
    ]
    task = keyword_route("The first one please", history).tasks[0]
    assert (task.specialist, task.action, task.department, task.slot_preference) == (
        "appointments", "reschedule", "cardiology", "The first one please",
    )
    assert keyword_route("The first one please").tasks[0].specialist == "knowledge"  # no context, no guess


OPTIONS = [
    {"slot_id": 11, "start_time": "2026-10-27T10:00+05:30"},  # Tuesday
    {"slot_id": 12, "start_time": "2026-10-27T11:30+05:30"},
    {"slot_id": 13, "start_time": "2026-10-28T10:00+05:30"},  # Wednesday
]


@pytest.mark.parametrize(
    ("preference", "slot_id"),
    [
        ("the first one", 11),
        ("second", 12),
        ("the last one please", 13),
        ("option 2", 12),
        ("Tuesday 11:30", 12),
        ("Wednesday", 13),
        ("11.30", 12),
        ("28 oct at 10:00", 13),
        ("2026-10-27 11:30", 12),  # copied exactly from the slot list
        ("2026-10-28 10:00 with Dr. Sameer Khan", 13),
        ("27/10 at 10:00", 11),
        ("28-10", 13),
    ],
)
def test_slot_choice_matches(preference, slot_id):
    assert match_slot(preference, OPTIONS) == slot_id


@pytest.mark.parametrize(
    "preference", ["10:00", "Tuesday", "sometime next month", "fifth", "9 pm", "2026-10-29 13:00", "28 oct at 11:30"]
)
def test_slot_choice_ambiguous_or_unmatched_returns_none(preference):
    assert match_slot(preference, OPTIONS) is None
