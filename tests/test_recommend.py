from fakes import sample_gym
from recommend import nearest, recommend


def gyms():
    return [
        sample_gym(id="cheap", name="Cheap", price_month=250000, amenities="trenajyor", free_trial=False, lat=41.30, lon=69.25),
        sample_gym(id="mid", name="Mid", price_month=430000, amenities="kardio, basseyn", free_trial=True, lat=41.35, lon=69.30),
        sample_gym(id="rich", name="Rich", price_month=900000, amenities="basseyn", free_trial=True, lat=41.20, lon=69.10),
        sample_gym(id="noprice", name="NoPrice", price_month=None, amenities="", free_trial=False, lat=None, lon=None),
    ]


def ids(matches):
    return [m.gym["id"] for m in matches]


def test_budget_excludes_expensive():
    result = ids(recommend(gyms(), "lose", "450", "all", limit=10))
    assert "rich" not in result and {"cheap", "mid", "noprice"} <= set(result)


def test_goal_and_trial_boost():
    result = recommend(gyms(), "lose", "any", "all", limit=10)
    assert result[0].gym["id"] in {"mid", "rich"}           # kardio/basseyn + bepul sinov
    assert any(key == "why_goal" for key, _ in result[0].reasons)


def test_district_filter():
    gs = gyms() + [sample_gym(id="y", name="Y", district_key="yunusobod", price_month=300000)]
    assert ids(recommend(gs, "other", "any", "district", district_key="yunusobod")) == ["y"]


def test_near_orders_by_distance_and_skips_without_location():
    result = recommend(gyms(), "other", "any", "near", user_pos=(41.30, 69.25), limit=10)
    assert ids(result)[0] == "cheap" and "noprice" not in ids(result)
    assert result[0].km is not None


def test_nearest():
    rows = nearest(gyms(), 41.35, 69.30)
    assert [g["id"] for g, _ in rows][0] == "mid"
    assert len(rows) == 3 and len(nearest(gyms(), 41.35, 69.30, limit=2)) == 2
