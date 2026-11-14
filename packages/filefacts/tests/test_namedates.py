from datetime import date

import pytest

from filefacts.namedates import find_dates, latest_date, parse_date


@pytest.mark.parametrize(
    ("text", "expected", "precision"),
    [
        ("2023-01-15", date(2023, 1, 15), "day"),
        ("20230115", date(2023, 1, 15), "day"),
        ("2023_1_5", date(2023, 1, 5), "day"),
        ("Jan 15, 2023", date(2023, 1, 15), "day"),
        ("15th March 2023", date(2023, 3, 15), "day"),
        ("March 2023", date(2023, 3, 31), "month"),  # a month counts as its last day
        ("2024 feb", date(2024, 2, 29), "month"),
        ("2023-06", date(2023, 6, 30), "month"),
        ("2021", date(2021, 12, 31), "year"),  # a bare year counts as 31 December
    ],
)
def test_reads_each_form_as_the_latest_date_it_can_mean(text, expected, precision):
    found = parse_date(text)
    assert found.date == expected
    assert found.precision == precision


def test_day_and_month_in_either_order_takes_the_later_reading():
    assert parse_date("01-02-2023").date == date(2023, 2, 1)  # 1 Feb, not 2 Jan
    assert parse_date("15.01.2023").date == date(2023, 1, 15)  # only one reading is valid
    assert parse_date("01-15-2023").date == date(2023, 1, 15)


def test_impossible_dates_are_left_out():
    assert find_dates("statement 2023-02-30") == []


def test_other_numbers_are_not_dates():
    assert find_dates("Form 1040 page 12") == []


def test_latest_date_in_a_name():
    assert latest_date("Statement 2023-01 to 2023-03").date == date(2023, 3, 31)
    assert latest_date("2021 return filed 2022-04-15").date == date(2022, 4, 15)
    assert latest_date("notes") is None
