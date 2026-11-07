import jsonschema
import pytest

from factory import model_card


def make(cfg):
    return model_card.new_card("demo_model", "Demo", "R", ["models/demo_model/m.R"],
                               cfg["gates"]["stages"])


def test_save_bumps_version_and_history(cfg):
    card = model_card.save(make(cfg), cfg["paths"]["model_cards"], "created")
    card["inventory"]["owner"] = "Credit Risk"
    model_card.save(card, cfg["paths"]["model_cards"], "owner set")
    loaded = model_card.load(cfg["paths"]["model_cards"], "demo_model")
    assert loaded["card_version"] == 2
    assert [h["note"] for h in loaded["history"]] == ["created", "owner set"]
    assert loaded["schema_version"] == model_card.SCHEMA_VERSION


def test_invalid_card_is_rejected(cfg):
    card = make(cfg)
    card["inventory"]["risk_tier"] = "extreme"
    with pytest.raises(jsonschema.ValidationError):
        model_card.save(card, cfg["paths"]["model_cards"], "bad")


def test_unknown_top_level_field_is_rejected(cfg):
    card = make(cfg)
    card["surprise"] = 1
    with pytest.raises(jsonschema.ValidationError):
        model_card.validate({**card, "card_version": 1})
