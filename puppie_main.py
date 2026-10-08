import os
import signal

# 見張りプログラム(puppie_watcher.py)は PUPPIE_STANDBY=1 を付けてこのプログラムを起動する。
# そのときは準備だけ済ませてマイクを止めたまま待機し、タッチの合図(SIGUSR1)が来るたびに
# 会話を始める/終える。合図は下の重いimportの最中にも届くので、いちばん先に受け口を作る。
# 単独で起動したとき(PUPPIE_STANDBYなし)は、すぐ会話を始めて1回で終了する。
STANDBY = os.getenv("PUPPIE_STANDBY") == "1"

class ConversationStop(BaseException):
    """会話中のタッチで会話を打ち切る合図。except Exceptionに捕まらないようBaseExceptionにしている"""

start_requested = not STANDBY
in_conversation = False
stopping = False  # さよなら音声に入ったらTrue。以降のタッチは無視する

def _handle_touch(signum, frame):
    global start_requested, stopping
    if not in_conversation:
        start_requested = True
    elif not stopping:
        stopping = True
        raise ConversationStop()

signal.signal(signal.SIGUSR1, _handle_touch)

import pyaudio
import webrtcvad
import time
import collections
import pygame
import boto3
from openai import OpenAI
from dotenv import load_dotenv
import io
import shutil
import wave
import socket
import subprocess
import threading

from logger import log_to_csv
import weather
import kiko_memory

# === 初期化 ===
load_dotenv()  # .envを読み込む

# SIGTERM（kill、systemdの停止シグナルなど）もKeyboardInterruptと同じ
# 終了処理（さよなら音声・ストリームのクローズ）に乗せる
def _handle_sigterm(signum, frame):
    global stopping
    stopping = True
    # さよなら音声の途中で2回目のSIGTERMが来ても中断されないよう、以降は無視する
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise KeyboardInterrupt()

signal.signal(signal.SIGTERM, _handle_sigterm)

PUPPIE_SETTING_TEXT = "Dein Name ist KIKO. Du hast eine Benutzeroberfläche aus Holz und wirst von der Bonamondo UG entwickelt, um als Gesprächspartner die Einsamkeit älterer Menschen zu lindern. Christian, der CEO von Bonamondo, ist ehemaliger Profisportler und kümmert sich um Business Development. Für Design und Entwicklung ist Akiko verantwortlich. Heute stellt Akiko gemeinsam mit dir das KIKO-Projekt vor. Bitte antworte in etwa 20 Wörtern und halte deine Antworten einfach und verständlich. Dies ist ein gesprochenes Gespräch: Man spricht mit dir und hört deine Antwort. Verwende deshalb Wörter wie „sagen“ oder „erzählen“, niemals „schreiben“ oder „lesen“."
FALLBACK_REPLY = "Entschuldigung, das habe ich gerade nicht verstanden. Kannst du das bitte noch einmal sagen?"
STOP_PHRASES = ["tschüss", "auf wiedersehen", "bis bald", "mach's gut"]
GPT_MODEL = 'gpt-5.4-nano'
# GPT_MODEL = 'gpt-4o-mini'
STT_MODEL = 'gpt-4o-mini-transcribe'
# STTが聞き取る言語。固定しないと短い発話を別の言語と取り違えることがある。
# .envに PUPPIE_STT_LANGUAGE=ja のように書けば変更できる（空にすると自動判定）
STT_LANGUAGE = os.getenv("PUPPIE_STT_LANGUAGE", "de")
# OpenAIは普段1〜2秒で返るが、稀に数十秒固まることがある。
# この秒数で打ち切って1回だけやり直す（無応答のまま待たせない）
STT_TIMEOUT_SEC = 6
LLM_TIMEOUT_SEC = 8
# STTに送る前に録音をOpusに圧縮する（WAVの約1/10）。上りの遅い回線では
# 送信時間が待ち時間の大半になるため。Piには sudo apt install opus-tools が必要
# 圧縮の手間(--comp)は既定の10だとPi Zero 2Wで7秒の発話に約1秒かかる。3と60msフレームなら約0.35秒で、
# 認識結果は変わらなかった(2026-10-06実測)
OPUS_BITRATE_KBPS = 24
GPT_MAX_TOKENS = 1000

# 音量（0.0〜1.0）。.envに PUPPIE_VOLUME=0.5 のように書けば調整できる
VOLUME = float(os.getenv("PUPPIE_VOLUME", "0.7"))

# ユーザーがこの秒数話さなかったら自動終了する。.envに PUPPIE_IDLE_TIMEOUT_SEC=60 のように書けば調整できる
IDLE_TIMEOUT_SEC = float(os.getenv("PUPPIE_IDLE_TIMEOUT_SEC", "180"))

RATE = 16000
CHUNK_DURATION_MS = 30
CHUNK_SIZE = int(RATE * CHUNK_DURATION_MS / 1000)
CHANNELS = 1

SILENCE_CHUNKS_TO_STOP = 20  # 20 * 30ms = 600ms の無音で発話終了とみなす
MAX_RECORD_SECONDS = 15      # 無音判定できない場合の安全上限
MAX_RECORD_CHUNKS = int(MAX_RECORD_SECONDS * 1000 / CHUNK_DURATION_MS)

# 分析用: 会話の一発目の発話が外国語に聞き取られる原因を調べるため、一発目の録音だけここに残す。
# 原因がわかって要らなくなったら、このフォルダごと消して keep_recording まわりを外せばよい
FIRST_RECORDING_DIR = "debug_first"

vad = webrtcvad.Vad(1)  # 感度（0〜3）
audio_interface = pyaudio.PyAudio()
audio_stream = audio_interface.open(format=pyaudio.paInt16,
                                    channels=CHANNELS,
                                    rate=RATE,
                                    input=True,
                                    start=False,  # 会話を始めるまでマイクは止めておく
                                    frames_per_buffer=CHUNK_SIZE)

pygame.mixer.init()
audio_playing = False

history = collections.deque(maxlen=10)

# GPT(STT兼用) & Polly clients
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
polly_client = boto3.client("polly", region_name="eu-central-1")

# === 関数 ===
def check_internet(host="8.8.8.8", port=53, timeout=3):
    """
    インターネット接続があるかをチェックする。
    host: デフォルトは Google Public DNS (8.8.8.8)
    port: DNSポート53
    timeout: 秒数
    戻り値: True = 接続あり / False = 接続なし
    """
    try:
        socket.setdefaulttimeout(timeout)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
        return True
    except socket.error:
        return False
    
# LEDは見張りプログラム(puppie_watcher.py)が持っている。この行を標準出力に出すと
# 見張り側が読み取ってLEDを切り替える（単独で起動したときはただのログになる）
# listening=緑点灯 / thinking=青の明滅 / speaking=青点灯 / error=赤点灯
def set_led(state):
    print(f"@@LED {state}", flush=True)

def is_speaking(audio_chunk):
    return vad.is_speech(audio_chunk, RATE)

def is_stop_command(transcript):
    lowered = transcript.lower()
    return any(phrase in lowered for phrase in STOP_PHRASES)

def play_goodbye():
    print("🛑 終了します。")
    set_led("speaking")
    pygame.mixer.music.load("./Voices/vicky_goodbye.wav")
    pygame.mixer.music.set_volume(VOLUME)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        time.sleep(0.1)

def play_audio(text):
    global audio_playing
    audio_playing = True
    audio_stream.stop_stream()

    try:
        # PollyでPCM取得（16kHz, mono, 16bit）
        t0 = time.perf_counter()
        response = polly_client.synthesize_speech(
            Text=text,
            OutputFormat="pcm",
            SampleRate="16000",
            VoiceId="Vicki",
            LanguageCode="de-DE"
        )
        print(f"⏱️ TTS合成: {time.perf_counter() - t0:.2f}s")

        # PCMをWAVファイルにラップ
        t0 = time.perf_counter()
        with wave.open("reply.wav", "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)   # 16bit = 2 bytes
            wav_file.setframerate(16000)
            wav_file.writeframes(response['AudioStream'].read())
        print(f"⏱️ WAV書き出し: {time.perf_counter() - t0:.2f}s")

        t0 = time.perf_counter()
        pygame.mixer.music.load("reply.wav")
        pygame.mixer.music.set_volume(VOLUME)
        set_led("speaking")
        pygame.mixer.music.play()
        print(f"⏱️ 再生開始まで: {time.perf_counter() - t0:.2f}s")
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    except Exception as e:
        print(f"⚠️ 音声再生エラー: {e}")
    finally:
        time.sleep(0.25)
        history.clear()
        audio_stream.start_stream()
        audio_playing = False

def encode_for_stt(raw):
    """
    録音(PCM)をSTT送信用のファイル(名前, 中身)にする。
    opusencでOpusに圧縮し、使えなければ無圧縮のWAVで送る。
    """
    try:
        t0 = time.perf_counter()
        result = subprocess.run(
            ["opusenc", "--quiet", "--raw", "--raw-rate", str(RATE), "--raw-chan", str(CHANNELS),
             "--bitrate", str(OPUS_BITRATE_KBPS), "--comp", "3", "--framesize", "60", "-", "-"],
            input=raw, capture_output=True, check=True, timeout=5,
        )
        print(f"⏱️ Opus圧縮: {time.perf_counter() - t0:.2f}s ({len(raw) / 1024:.0f}KB → {len(result.stdout) / 1024:.0f}KB)")
        return ("speech.ogg", result.stdout)
    except Exception as e:
        print(f"⚠️ Opus圧縮できないのでWAVで送ります: {e}")

    # OpenAIのSTTはファイル形式で受け取るため、PCMをメモリ上でWAVにラップする
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(RATE)
        wav_file.writeframes(raw)
    return ("speech.wav", wav_buffer.getvalue())

def listen_and_transcribe(keep_recording=False):
    """
    無音になるまでローカルで録音してから、録音全体を1回だけ
    OpenAIのSTTに送る（非ストリーミング）。テザリング回線など
    不安定なネットワークでも、長時間の持続接続に依存しないため安定しやすい。
    """
    frames = []
    silence_count = 0
    t_start = time.perf_counter()

    while audio_stream.is_active():
        chunk = audio_stream.read(CHUNK_SIZE, exception_on_overflow=False)
        frames.append(chunk)

        if is_speaking(chunk):
            silence_count = 0
        else:
            silence_count += 1

        if silence_count > SILENCE_CHUNKS_TO_STOP or len(frames) > MAX_RECORD_CHUNKS:
            break

    raw = b"".join(frames)
    record_elapsed = time.perf_counter() - t_start
    set_led("thinking")

    # デバッグ用: 実際に何を録音したか確認するため直近の録音を保存
    with wave.open("/tmp/debug_capture.wav", "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(RATE)
        wav_file.writeframes(raw)
    # 実時間が録音長よりはっきり長ければ、処理が追いつかず音が欠けている
    print(f"🐛 録音長: {len(frames)}チャンク ({len(frames) * CHUNK_DURATION_MS / 1000:.2f}s、実時間 {record_elapsed:.2f}s)")
    if keep_recording:
        try:
            os.makedirs(FIRST_RECORDING_DIR, exist_ok=True)
            kept_path = os.path.join(FIRST_RECORDING_DIR, time.strftime("first_%Y%m%d_%H%M%S.wav"))
            shutil.copy("/tmp/debug_capture.wav", kept_path)
            print(f"🐛 一発目の録音を保存: {kept_path}")
        except OSError as e:
            print(f"⚠️ 一発目の録音を保存できません: {e}")

    stt_file = encode_for_stt(raw)

    try:
        t0 = time.perf_counter()
        response = openai_client.with_options(timeout=STT_TIMEOUT_SEC, max_retries=1).audio.transcriptions.create(
            model=STT_MODEL,
            file=stt_file,
            **({"language": STT_LANGUAGE} if STT_LANGUAGE else {}),
        )
        print(f"⏱️ STT: {time.perf_counter() - t0:.2f}s")
        return response.text.strip()
    except Exception as e:
        print(f"⚠️ STTエラー: {e}")
        return ""

def warmup_apis():
    """
    起動時に各外部APIへダミーリクエストを送っておく。
    TLSハンドシェイク等の初回接続コストを会話開始前に済ませることで、
    ユーザーとの最初のやり取りが不自然に遅く感じる問題を防ぐ。
    """
    t0 = time.perf_counter()
    try:
        openai_client.chat.completions.create(
            model=GPT_MODEL,
            messages=[{"role": "user", "content": "Hallo"}],
            max_completion_tokens=100,
        )
    except Exception as e:
        print(f"⚠️ OpenAIウォームアップ失敗: {e}")
    print(f"⏱️ ウォームアップ(OpenAI): {time.perf_counter() - t0:.2f}s")

    t0 = time.perf_counter()
    try:
        polly_client.synthesize_speech(
            Text="Hallo",
            OutputFormat="pcm",
            SampleRate="16000",
            VoiceId="Vicki",
            LanguageCode="de-DE",
        )
    except Exception as e:
        print(f"⚠️ Pollyウォームアップ失敗: {e}")
    print(f"⏱️ ウォームアップ(Polly): {time.perf_counter() - t0:.2f}s")

def chat_with_assistant(client, user_input, conversation_history):
    # ユーザーの入力を履歴に追加
    history_length = len(conversation_history)
    conversation_history.append({"role": "user", "content": user_input})

    # OpenAI API呼び出し
    try:
        llm = client.with_options(timeout=LLM_TIMEOUT_SEC, max_retries=1)
        t0 = time.perf_counter()
        response = llm.chat.completions.create(model=GPT_MODEL,
            messages=conversation_history, max_completion_tokens=GPT_MAX_TOKENS,
            tools=weather.TOOLS,
            )
        print(f"⏱️ LLM: {time.perf_counter() - t0:.2f}s")
        message = response.choices[0].message

        # 天気を聞かれたとLLMが判断したら、天気を調べて結果を渡し、もう一度答えさせる
        if message.tool_calls:
            conversation_history.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {"id": call.id, "type": "function",
                     "function": {"name": call.function.name, "arguments": call.function.arguments}}
                    for call in message.tool_calls
                ],
            })
            for call in message.tool_calls:
                t0 = time.perf_counter()
                result = weather.call_tool(call.function.name, call.function.arguments)
                print(f"⏱️ 天気({call.function.arguments}): {time.perf_counter() - t0:.2f}s")
                conversation_history.append({"role": "tool", "tool_call_id": call.id, "content": result})
            t0 = time.perf_counter()
            response = llm.chat.completions.create(model=GPT_MODEL,
                messages=conversation_history, max_completion_tokens=GPT_MAX_TOKENS,
                )
            print(f"⏱️ LLM(天気の結果から返答): {time.perf_counter() - t0:.2f}s")
            message = response.choices[0].message

        # アシスタントの応答を履歴に追加
        assistant_reply = message.content
        conversation_history.append({"role": "assistant", "content": assistant_reply})

        log_to_csv("User", user_input)
        log_to_csv("Assistant", assistant_reply)

        # 応答を返す
        # print(conversation_history)
        return assistant_reply
    
    except Exception as e:
        print(f"⚠️ Chat APIエラー: {e}")
        # 途中まで足した履歴（返答のない質問や天気の呼び出し）を残すと次の呼び出しが壊れるので巻き戻す
        del conversation_history[history_length:]
        return FALLBACK_REPLY

# === 会話1回ぶん ===
def run_conversation():
    global in_conversation, stopping
    stopping = False
    in_conversation = True
    conversation_history = []
    try:
        set_led("starting")
        if check_internet():
            print("✅ Connected to the Internet.")
        else:
            print("❌ No internet connection.")
            set_led("error")
            # ここで ready.wav の代わりに「ごめんね、ネットがないよ」を再生
            pygame.mixer.music.load("./Voices/vicky_noInternet.wav")
            pygame.mixer.music.set_volume(VOLUME)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():   # 再生が終わるまで待つ
                time.sleep(0.1)
            return

        # 待機中に接続は冷えるので会話のたびに温め直す。終わるのを待たずに聞き始める
        # （最初の一言を話している間に終わる）
        threading.Thread(target=warmup_apis, daemon=True).start()

        print("🎤 Puppie started. Waiting the user to speak...")

        pygame.mixer.music.load("./Voices/vicky_ready.wav")
        pygame.mixer.music.set_volume(VOLUME)
        set_led("speaking")
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)

        history.clear()
        audio_stream.start_stream()
        set_led("listening")

        # 設定文に、今日の日付とこれまでの会話で覚えたことを足す
        defaultsetting = {"role": "system", "content": PUPPIE_SETTING_TEXT + kiko_memory.for_system_prompt()}
        conversation_history.append(defaultsetting)

        last_activity = time.monotonic()
        first_utterance = True  # 最初に聞き取れた発話までの録音を分析用に残す

        while True:
            # ★ 再生中 or ストリーム停止中はreadしない
            if audio_playing or not audio_stream.is_active():
                time.sleep(0.01)
                continue

            if time.monotonic() - last_activity > IDLE_TIMEOUT_SEC:
                print(f"💤 {IDLE_TIMEOUT_SEC:.0f}秒間発話がなかったので終了します")
                break

            audio = audio_stream.read(CHUNK_SIZE, exception_on_overflow=False)

            is_voice = is_speaking(audio)
            history.append(1 if is_voice else 0)

            if sum(history) > 6:
                print("🗣️ ユーザーが話し始めました")
                try:
                    transcript = listen_and_transcribe(keep_recording=first_utterance)
                    history.clear()
                    print(f"✍️ 認識結果: {transcript if transcript else '(空振り/認識できず)'}")
                    if transcript and is_stop_command(transcript):
                        print("👋 さよならコマンドを検知しました")
                        break
                    if transcript:
                        first_utterance = False
                        reply = chat_with_assistant(openai_client, transcript, conversation_history)
                        print(f"🤖 Puppie: {reply}")
                        play_audio(reply)
                        # 雑音による空振りでは延長せず、認識できた発話への返答が終わった時点から数え直す
                        last_activity = time.monotonic()
                except Exception as e:
                    # 想定外のエラーでも会話ループ自体は止めない
                    print(f"⚠️ 予期しないエラー: {e}")
                # 返答を話し終えた後も、空振り・エラーの後も、ここで「どうぞ話して」に戻す
                set_led("listening")

        # さよなら音声の途中でタッチされても、二重に再生しない
        stopping = True
        play_goodbye()

    except ConversationStop:
        print("👆 タッチで会話を終了します")
        play_goodbye()

    except KeyboardInterrupt:
        play_goodbye()
        raise

    finally:
        if audio_stream.is_active():
            audio_stream.stop_stream()
        in_conversation = False
        # さよならを言い終えてから、この会話で覚えておくことを保存する（1〜2秒）。
        # この間のタッチは次の会話の合図として受け付けられる
        kiko_memory.update(openai_client, GPT_MODEL, conversation_history)

# === メインループ ===
try:
    while True:
        if STANDBY:
            set_led("idle")
            print("💤 待機中。タッチを待っています...")
            while not start_requested:
                time.sleep(0.05)
        start_requested = False
        run_conversation()
        if not STANDBY:
            break

except KeyboardInterrupt:
    pass

finally:
    audio_stream.close()
    audio_interface.terminate()
