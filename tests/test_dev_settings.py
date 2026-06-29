"""dev_settings edition ordering tests."""

from dev_settings import edition_at_least


def test_edition_at_least_standard_vs_professional():
    assert edition_at_least('Standard', 'Standard') is True
    assert edition_at_least('Standard', 'Professional') is False
    assert edition_at_least('Professional', 'Professional') is True
    assert edition_at_least('Professional', 'Standard') is True


def test_edition_production_maps_to_standard():
    assert edition_at_least('Production', 'Standard') is True
    assert edition_at_least('Production', 'Professional') is False
