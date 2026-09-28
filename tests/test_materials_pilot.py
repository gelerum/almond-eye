from scripts.summarize_materials_pilot import score_pairs


def test_unknown_reference_does_not_count_as_negative():
    result = score_pairs([(True, True), (True, False), (False, True), (False, False), (None, True)])
    assert {key:result[key] for key in ('tp','fp','fn','tn','excluded')} == dict(tp=1,fp=1,fn=1,tn=1,excluded=1)
    assert result['precision'] == result['recall'] == 0.5


def test_absent_positive_support_does_not_produce_perfect_recall():
    result = score_pairs([(False, False), (None, False)])
    assert result['precision'] is None
    assert result['recall'] is None
