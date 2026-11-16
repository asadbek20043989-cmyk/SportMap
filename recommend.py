"""Aqlli tavsiya: maqsad, byudjet va joylashuvga qarab zallarni ballaydi (sof mantiq)."""
from dataclasses import dataclass, field

from utils import haversine_km, fmt_km

# maqsad -> qulaylik kalit so'zlari (jadvaldagi 'amenities' va 'notes' ichidan qidiriladi)
GOAL_KEYWORDS = {
    "lose": [
        "kardio", "cardio", "yugurish", "yurish", "treadmill", "beg", "дорожк", "кардио",
        "aerobika", "aerobic", "аэроб", "zumba", "basseyn", "бассейн", "pool", "sauna", "сауна",
        "guruh", "group", "групп", "hiit", "crossfit", "kross",
    ],
    "muscle": [
        "trenajyor", "тренаж", "gym", "shtanga", "штанг", "gantel", "гантел", "dumbbell", "barbell",
        "erkin og'irlik", "free weight", "силов", "bodybuilding", "kuch", "powerlifting", "kross",
    ],
    "healthy": [
        "yoga", "йога", "pilates", "пилатес", "basseyn", "бассейн", "pool", "sauna", "сауна",
        "hammom", "spa", "спа", "stretching", "streching", "растяжк", "massaj", "массаж", "fitnes", "фитнес",
    ],
}

BUDGET_LIMITS = {"300": 300_000, "450": 450_000, "600": 600_000}


@dataclass
class Match:
    gym: dict
    score: float
    km: float | None = None
    reasons: list = field(default_factory=list)  # [(texts kaliti, kwargs), ...]


def _text_of(g: dict) -> str:
    return f"{g.get('amenities', '')} {g.get('notes', '')}".lower()


def goal_hits(g: dict, goal: str) -> int:
    text = _text_of(g)
    return sum(1 for kw in GOAL_KEYWORDS.get(goal, []) if kw in text)


def recommend(
    gyms: list[dict],
    goal: str,
    budget: str,
    where: str,
    district_key: str | None = None,
    user_pos: tuple[float, float] | None = None,
    limit: int = 3,
) -> list[Match]:
    """where: 'near' | 'district' | 'all'. Byudjetdan oshadigan zallar chiqarib tashlanadi."""
    limit_som = BUDGET_LIMITS.get(budget)
    matches: list[Match] = []
    for g in gyms:
        score = 0.0
        reasons: list = []
        km = None

        if where == "district":
            if g["district_key"] != district_key:
                continue
            score += 2
            reasons.append(("why_district", {}))
        elif where == "near":
            if user_pos is None or g.get("lat") is None or g.get("lon") is None:
                continue
            km = haversine_km(user_pos[0], user_pos[1], g["lat"], g["lon"])
            score += max(0.0, 4.0 - km / 1.5)
            reasons.append(("why_near", {"km": fmt_km(km)}))

        price = g.get("price_month")
        if limit_som is not None:
            if price is None:
                score += 0.5
                reasons.append(("why_budget_unknown", {}))
            elif price > limit_som:
                continue
            else:
                score += 3 + (limit_som - price) / limit_som  # arzonroq = biroz yuqori
                reasons.append(("why_budget", {}))

        hits = goal_hits(g, goal)
        if hits:
            score += min(hits, 3) * 0.8
            reasons.append(("why_goal", {}))

        if g.get("free_trial"):
            score += 1
            reasons.append(("why_trial", {}))

        matches.append(Match(gym=g, score=score, km=km, reasons=reasons))

    matches.sort(key=lambda m: (-m.score, m.km if m.km is not None else 0, m.gym["name"]))
    return matches[:limit]


def nearest(gyms: list[dict], lat: float, lon: float, limit: int | None = None) -> list[tuple[dict, float]]:
    rows = [
        (g, haversine_km(lat, lon, g["lat"], g["lon"]))
        for g in gyms
        if g.get("lat") is not None and g.get("lon") is not None
    ]
    rows.sort(key=lambda r: r[1])
    return rows[:limit] if limit else rows
