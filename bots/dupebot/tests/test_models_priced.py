from dupebot.analyze import MODEL_CHOICES
from finops.pricing import PRICES


def test_every_menu_model_has_a_price():
    assert {c.model for c in MODEL_CHOICES} <= set(PRICES)
