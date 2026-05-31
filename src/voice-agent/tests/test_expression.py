from lipsync.expression import expression_for_text, ExpressionEngine


def test_positive_text_smiles():
    w = expression_for_text("That is wonderful, I am so happy for you!")
    assert w.get("target_37", 0) > 0 or w.get("target_38", 0) > 0   # mouthSmile
    assert all(0.0 <= v <= 1.0 for v in w.values())
    assert all(k.startswith("target_") for k in w)


def test_negative_text_furrows_brows():
    w = expression_for_text("This is terrible, broken, and awful.")
    assert w.get("target_1", 0) > 0 or w.get("target_2", 0) > 0      # browDown


def test_question_raises_outer_brows():
    w = expression_for_text("Are you absolutely sure about that?")
    assert w.get("target_3", 0) > 0 or w.get("target_4", 0) > 0      # browOuterUp


def test_exclamation_widens_eyes():
    w = expression_for_text("Look out!")
    assert w.get("target_17", 0) > 0 or w.get("target_18", 0) > 0    # eyeWide


def test_neutral_statement_is_empty():
    assert expression_for_text("the file is in that folder") == {}


def test_empty_text_is_empty():
    assert expression_for_text("") == {}
    assert expression_for_text("   ") == {}


def test_engine_holds_while_speaking_clears_idle():
    eng = ExpressionEngine()
    eng.set_pending_text("Fantastic, brilliant work!")
    assert eng.frame(speaking=True)              # non-empty while speaking
    assert eng.frame(speaking=False) == {}       # cleared when idle


def test_caps_without_punctuation_is_emphatic():
    # >=2 all-caps words (>=2 letters) read as emphasis even with no '!'.
    w = expression_for_text("THIS IS AMAZING")
    assert w.get("target_17", 0) > 0 or w.get("target_18", 0) > 0   # eyeWide (emphatic)
    assert "!" not in "THIS IS AMAZING"                              # the trigger was CAPS, not '!'
