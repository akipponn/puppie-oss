"""
KIKOの記憶。会話が終わるたびに、覚えておくことをLLMに短い箇条書きにまとめさせて
テキストファイルに保存し、次の会話の最初に設定文と一緒にLLMへ渡す。
忘れさせたいときは MEMORY_FILE を消す（中身を手で直してもよい）。
"""
import datetime
import os

MEMORY_FILE = "kiko_memory.txt"
MEMORY_MAX_LINES = 30
MEMORY_TIMEOUT_SEC = 15

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

UPDATE_INSTRUCTIONS = (
    "Du pflegst das Gedächtnis von KIKO, einem sprechenden Begleiter für ältere Menschen. "
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
    """設定文の後ろに足す文章（今日の日付と、これまでの記憶）"""
    text = f"\n\nHeute ist {_today()}."
    memory = load()
    if memory:
        text += ("\n\nDas weißt du aus früheren Gesprächen mit dieser Person. "
                 "Erwähne es nur, wenn es zum Gespräch passt:\n" + memory)
    return text


def update(client, model, conversation_history):
    """終わった会話から記憶を更新する。失敗したら今の記憶をそのまま残す"""
    lines = []
    for message in conversation_history:
        if not isinstance(message.get("content"), str):
            continue
        if message["role"] == "user":
            lines.append(f"Person: {message['content']}")
        elif message["role"] == "assistant":
            lines.append(f"KIKO: {message['content']}")
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
        print(f"⚠️ 記憶の更新エラー: {e}")
        return
    if not new_memory:
        return

    new_memory = "\n".join(new_memory.splitlines()[:MEMORY_MAX_LINES])
    # 書いている途中で電源が切れても記憶が壊れないよう、別名で書いてから置き換える
    temp_file = MEMORY_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        file.write(new_memory + "\n")
    os.replace(temp_file, MEMORY_FILE)
    print(f"🧠 記憶を更新しました（{len(new_memory.splitlines())}行）")
