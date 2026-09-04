"""The roster reader: `person_id == slug(name)` is a PRODUCT contract (SPEC Q1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from arrival.research import RosterError, load_roster
from arrival.util import slug

pytestmark = pytest.mark.ticket("T-6")

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "roster_synthetic.yaml"


def test_the_committed_synthetic_roster_is_two_people_keyed_by_slug():
    people = load_roster(FIXTURE)

    assert [p.person_id for p in people] == ["marisol-trevino", "anselm-kettleby"]
    for person in people:
        assert person.person_id == slug(person.name), (
            "SPEC Q1 pins person_id == slug(name); the fixture must not state ids of its own"
        )
        assert person.details


def test_ids_come_from_the_name_even_when_it_needs_normalising(tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text(
        "people:\n"
        "  - name: \"Jane O'Neil-Ruiz\"\n"
        "    details: [investor]\n"
        "  - name: \"José Ángel Núñez\"\n"
        "    details: [writer]\n",
        encoding="utf-8",
    )

    people = load_roster(path)

    assert [p.person_id for p in people] == ["jane-oneil-ruiz", "jose-angel-nunez"]


def test_a_duplicate_name_is_disambiguated_by_its_first_detail(tmp_path):
    """`contracts.PersonRef`: slug(name) [+ "-" + slug(details[0])] on collision."""
    path = tmp_path / "roster.yaml"
    path.write_text(
        "people:\n"
        "  - name: Marisol Trevino\n"
        "    details: [platform lead]\n"
        "  - name: Marisol Trevino\n"
        "    details: [ceramicist, Marfa]\n",
        encoding="utf-8",
    )

    people = load_roster(path)

    assert [p.person_id for p in people] == ["marisol-trevino", "marisol-trevino-ceramicist"]
    assert len({p.person_id for p in people}) == 2


def test_loose_but_real_shapes_are_accepted(tmp_path):
    """A hand-written roster: a bare list, a bare string, a single detail as a scalar."""
    path = tmp_path / "roster.yaml"
    path.write_text(
        "- Marisol Trevino\n- name: Anselm Kettleby\n  details: Austin\n",
        encoding="utf-8",
    )

    people = load_roster(path)

    assert [p.person_id for p in people] == ["marisol-trevino", "anselm-kettleby"]
    assert people[0].details == []
    assert people[1].details == ["Austin"]


def test_entries_that_name_nobody_are_dropped_not_guessed(tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text(
        "people:\n"
        "  - name: ''\n"
        "    details: [nothing]\n"
        "  - 17\n"
        "  - name: Marisol Trevino\n",
        encoding="utf-8",
    )

    people = load_roster(path)

    assert [p.person_id for p in people] == ["marisol-trevino"]


@pytest.mark.parametrize(
    "text",
    [
        "people: []\n",
        "people:\n",
        "just a string\n",
        "",
    ],
)
def test_a_roster_with_no_people_is_an_error_not_an_empty_build(tmp_path, text):
    path = tmp_path / "roster.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(RosterError):
        load_roster(path)


def test_a_missing_or_unparsable_roster_is_a_rostererror(tmp_path):
    with pytest.raises(RosterError):
        load_roster(tmp_path / "nope.yaml")

    bad = tmp_path / "bad.yaml"
    bad.write_text("people:\n  - name: [unclosed\n", encoding="utf-8")
    with pytest.raises(RosterError):
        load_roster(bad)


def test_a_declared_person_id_cannot_escape_the_output_directory(tmp_path):
    """The id becomes `out_dir/{person_id}.json`, and a roster is hand-written YAML."""
    path = tmp_path / "roster.yaml"
    path.write_text(
        "people:\n"
        "  - name: Marisol Trevino\n"
        "    person_id: ../../../etc/passwd\n"
        "  - name: Anselm Kettleby\n"
        "    person_id: nested/id\n",
        encoding="utf-8",
    )

    people = load_roster(path)

    for person in people:
        assert "/" not in person.person_id and ".." not in person.person_id
        assert person.person_id == slug(person.person_id)


def test_a_name_that_slugs_to_nothing_is_dropped_rather_than_keyed_as_empty(tmp_path):
    """An empty person_id would write `.json` into the dossier directory."""
    path = tmp_path / "roster.yaml"
    path.write_text(
        "people:\n  - name: '???'\n  - name: Marisol Trevino\n", encoding="utf-8"
    )

    people = load_roster(path)

    assert [p.person_id for p in people] == ["marisol-trevino"]
