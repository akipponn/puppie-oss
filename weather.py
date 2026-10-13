"""
Weather forecast. Uses Open-Meteo (https://open-meteo.com). No API key needed.
The LLM calls get_weather when it decides it has been asked about the weather (function calling).
"""
import datetime
import json
import os

import httpx  # installed together with the openai package

# The city used when asked about the weather without naming one. Change it by writing e.g. PUPPIE_HOME_CITY=Berlin in .env
HOME_CITY = os.getenv("PUPPIE_HOME_CITY", "Leipzig")
WEATHER_TIMEOUT_SEC = 4
FORECAST_DAYS = 7

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

# WMO weather code → German for reading aloud
WEATHER_CODES = {
    0: "klarer Himmel", 1: "überwiegend klar", 2: "teilweise bewölkt", 3: "bewölkt",
    45: "Nebel", 48: "Nebel mit Reif",
    51: "leichter Nieselregen", 53: "Nieselregen", 55: "starker Nieselregen",
    56: "gefrierender Nieselregen", 57: "gefrierender Nieselregen",
    61: "leichter Regen", 63: "Regen", 65: "starker Regen",
    66: "gefrierender Regen", 67: "gefrierender Regen",
    71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall", 77: "Schneegriesel",
    80: "leichte Regenschauer", 81: "Regenschauer", 82: "heftige Regenschauer",
    85: "Schneeschauer", 86: "starke Schneeschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Gewitter mit Hagel",
}

# Description of the tool passed to the LLM
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "Liefert das aktuelle Wetter und die Vorhersage für die nächsten Tage (heute, morgen, Wochenende) "
            f"für eine Stadt. Wird keine Stadt genannt, gilt {HOME_CITY}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": f"Name der Stadt, z. B. {HOME_CITY}"},
            },
        },
    },
}]

_location_cache = {}  # city name → (display name, latitude, longitude). Do not look up the same city twice


def _get_json(url, params):
    response = httpx.get(url, params=params, timeout=WEATHER_TIMEOUT_SEC)
    response.raise_for_status()
    return response.json()


def _find_location(city):
    key = city.strip().lower()
    if key not in _location_cache:
        data = _get_json("https://geocoding-api.open-meteo.com/v1/search",
                         {"name": city, "count": 1, "language": "de", "format": "json"})
        results = data.get("results")
        if not results:
            return None
        place = results[0]
        name = place["name"]
        if place.get("country"):
            name += f" ({place['country']})"
        _location_cache[key] = (name, place["latitude"], place["longitude"])
    return _location_cache[key]


def _describe(code):
    return WEATHER_CODES.get(code, "unbekannt")


def get_weather(city=None):
    """Return the weather as text (in German) to pass to the LLM. On failure, return text that says so"""
    city = (city or "").strip() or HOME_CITY
    try:
        location = _find_location(city)
        if location is None:
            return f"Die Stadt {city} wurde nicht gefunden."
        name, latitude, longitude = location
        data = _get_json("https://api.open-meteo.com/v1/forecast", {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": FORECAST_DAYS,
        })
    except Exception as e:
        print(f"⚠️ Weather fetch error: {e}")
        return "Die Wetterdaten sind gerade nicht erreichbar."

    current = data["current"]
    lines = [f"Wetter für {name}. Jetzt: {round(current['temperature_2m'])} Grad, {_describe(current['weather_code'])}."]
    daily = data["daily"]
    for i, day in enumerate(daily["time"]):
        date = datetime.date.fromisoformat(day)
        label = WEEKDAYS[date.weekday()]
        if i == 0:
            label = f"Heute, {label}"
        elif i == 1:
            label = f"Morgen, {label}"
        line = (f"{label} {date.day}.{date.month}.: {_describe(daily['weather_code'][i])}, "
                f"{round(daily['temperature_2m_min'][i])} bis {round(daily['temperature_2m_max'][i])} Grad")
        rain = daily["precipitation_probability_max"][i]
        if rain is not None:
            line += f", Regenwahrscheinlichkeit {rain} Prozent"
        lines.append(line)
    return "\n".join(lines)


def call_tool(name, arguments_json):
    """Run a call from the LLM and return the resulting text"""
    if name != "get_weather":
        return "Unbekanntes Werkzeug."
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        arguments = {}
    return get_weather(arguments.get("city"))


if __name__ == "__main__":
    import sys
    print(get_weather(sys.argv[1] if len(sys.argv) > 1 else None))
