from evaluations.checks import check_expected, extract_numbers


def test_extract_numbers_handles_commas_and_units():
    assert {8439765, 29} <= extract_numbers("東北: 8,439,765円（29.8%）")


def test_check_expected_passes_when_everything_matches():
    exp = {"tools_any_of": ["a"], "must_contain_numbers": [28298695], "must_not_contain_numbers": [30500770]}
    assert check_expected(exp, "売上合計は28,298,695円", ["a"]) == []


def test_check_expected_reports_each_failure():
    exp = {"tools_any_of": ["a"], "must_contain_numbers": [1000], "must_not_contain_numbers": [30500770],
           "must_contain_text": ["東北"]}
    failures = check_expected(exp, "30,500,770円です", [])
    assert len(failures) == 4


def test_check_expected_answer_starts_with():
    exp = {"answer_starts_with": "結論"}
    assert check_expected(exp, "結論: 東北が1位", []) == []
    assert len(check_expected(exp, "東北が1位です", [])) == 1
