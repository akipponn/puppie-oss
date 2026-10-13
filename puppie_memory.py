"""
Puppie's memory. After each conversation, the LLM sums up what is worth remembering as short bullet points.
They are saved to a text file and passed to the LLM together with the system prompt at the start of the next conversation.
To make Puppie forget, delete MEMORY_FILE (you can also edit its contents by hand).
"""
import datetime
import os

MEMORY_FILE = "puppie_memory.txt"
MEMORY_MAX_LINES = 30
MEMORY_TIMEOUT_SEC = 15

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

UPDATE_INSTRUCTIONS = (
    "Du pflegst das Gedächtnis von Puppie, einem sprechenden Begleiter. "
    "Du bekommst das bisherige Gedächtnis und die Mitschrift eines neuen Gesprächs. "
    "Gib das aktualisierte Gedächtnis zurück: kurze Stichpunkte auf Deutsch, eine Zeile pro Stichpunkt, "
    f"höchstens {MEMORY_MAX_LINES} Zeilen, ohne Einleitung. "
    "Merke dir, was in späteren Gesprächen nützlich ist: Name, Familie und Freunde, Vorlieben, Gewohnheiten, "
    "Befinden, Pläne und worüber zuletzt gesprochen wurde (mit Datum). "
    "Schreibe Termine mit Datum statt mit Wörtern wie morgen oder Samstag. "
    "Behalte bisherige Stichpunkte, außer sie sind überholt oder unwichtig geworden. "
    "Die Mitschrift stammt aus einer Spracherkennung und enthält Fehler: Unverständliches und Zweifelhaftes "
    "merkst du dir nicht. Das Wetter merkst du dir nicht. "
    "Gibt es nichts Neues, gib das bisherige Gedächtnis unverändert zurück."
)


def _today():
    today = datetime.date.today()
    return f"{WEEKDAYS[today.weekday()]}, {today.day}.{today.month}.{today.year}"


def load():
    try:
        with open(MEMORY_FILE, encoding="utf-8") as file:
            return file.read().strip()
    except FileNotFoundError:
        return ""


def for_system_prompt():
    """Text to add after the system prompt (today's date and the memory so far)"""
    text = f"\n\nHeute ist {_today()}."
    memory = load()
    if memory:
        text += ("\n\nDas weißt du aus früheren Gesprächen mit dieser Person. "
                 "Erwähne es nur, wenn es zum Gespräch passt:\n" + memory)
    return text


def update(client, model, conversation_history):
    """Update the memory from the conversation that just ended. If it fails, keep the current memory as is"""
    lines = []
    for message in conversation_history:
        if not isinstance(message.get("content"), str):
            continue
        if message["role"] == "user":
            lines.append(f"Person: {message['content']}")
        elif message["role"] == "assistant":
            lines.append(f"Puppie: {message['content']}")
    if not any(line.startswith("Person:") for line in lines):
        return

    old_memory = load()
    try:
        response = client.with_options(timeout=MEMORY_TIMEOUT_SEC, max_retries=1).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": UPDATE_INSTRUCTIONS},
                {"role": "user", "content": (
                    f"Heute ist {_today()}.\n\n"
                    f"Bisheriges Gedächtnis:\n{old_memory or '(noch leer)'}\n\n"
                    "Neues Gespräch:\n" + "\n".join(lines)
                )},
            ],
            max_completion_tokens=2000,
        )
        new_memory = (response.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"⚠️ Memory update error: {e}")
        return
    if not new_memory:
        return

    new_memory = "\n".join(new_memory.splitlines()[:MEMORY_MAX_LINES])
    # Write under another name and then replace, so the memory is not corrupted if power is lost mid-write
    temp_file = MEMORY_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        file.write(new_memory + "\n")
    os.replace(temp_file, MEMORY_FILE)
    print(f"🧠 Memory updated ({len(new_memory.splitlines())} lines)")
