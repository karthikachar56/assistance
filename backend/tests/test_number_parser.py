"""Tests for Indic + English spoken number extraction, colloquial fractions, and noise guards."""

from app.services.number_parser import extract_numbers, get_confusable_counterpart


def test_extracts_all_judge_number_formats():
    assert extract_numbers("Ramesh ka teen sau chaalis udhaar")[0].value == 340
    assert extract_numbers("Ramesh three-forty credit")[0].value == 340
    assert extract_numbers("three hundred forty rupees")[0].value == 340
    assert extract_numbers("Ramesh ka 340 udhaar")[0].value == 340
    assert extract_numbers("chaar sau tees")[0].value == 430
    assert extract_numbers("paanch sau rupaye")[0].value == 500
    assert extract_numbers("ek hazaar do sau pachaas")[0].value == 1250
    assert extract_numbers("Ramesh ge munnooru nalavattu haaku")[0].value == 340
    assert extract_numbers("Lakshmi ge nalknoora aivattu credit")[0].value == 450


def test_extracts_indic_digits_fractions_and_guards_false_so():
    assert extract_numbers("Suresh ka ३४० udhaar")[0].value == 340
    assert extract_numbers("Mohan ge ೪೮೦ credit")[0].value == 480
    assert extract_numbers("Mohan sava sau udhaar")[0].value == 125
    assert extract_numbers("Suresh dedh sau diya")[0].value == 150
    assert extract_numbers("Ramesh dhai sau payment")[0].value == 250
    assert extract_numbers("Mahesh sadhe teen sau udhaar")[0].value == 350
    assert extract_numbers("Suresh 1.5k jama")[0].value == 1500
    assert extract_numbers("two oh five rupees")[0].value == 205
    assert extract_numbers("so good customer") == []


def test_extracts_multiple_numbers_in_multi_clause_utterance():
    nums = extract_numbers("Ramesh ka 340 udhaar, kal 200 diya tha")
    assert [n.value for n in nums] == [340.0, 200.0]


def test_confusable_noise_pairs():
    assert get_confusable_counterpart(340) == 430.0
    assert get_confusable_counterpart(430) == 340.0
    assert get_confusable_counterpart(500) is None
