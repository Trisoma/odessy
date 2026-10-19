from ledger.totals import sum_amounts


def test_sum_of_whole_amounts():
    assert sum_amounts([{"amount": "2"}, {"amount": "3"}]) == 5


def test_sum_keeps_cents():
    assert sum_amounts([{"amount": "1.50"}, {"amount": "2.25"}]) == 3.75
