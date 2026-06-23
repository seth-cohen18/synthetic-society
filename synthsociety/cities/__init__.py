"""City registry. Cities self-register on import via @register."""

_CITIES = {}


def register(cls):
    _CITIES[cls.id] = cls
    return cls


def get_city(city_id: str):
    if city_id not in _CITIES:
        raise KeyError(f"Unknown city: {city_id}. Known: {list(_CITIES)}")
    return _CITIES[city_id]()


def list_cities() -> list:
    return [cls() for cls in _CITIES.values()]


# Import city packages so they self-register. Insertion order = display order.
# Failures (e.g. a city still under construction) must not break the launcher.
for _m in ("census", "forum", "arena"):
    try:
        __import__(f"synthsociety.cities.{_m}")
    except Exception:
        pass
