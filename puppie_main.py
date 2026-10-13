import os
import signal

# The watcher (puppie_watcher.py) starts this program with PUPPIE_STANDBY=1.
# In that case it only gets ready and waits with the microphone stopped; each touch signal (SIGUSR1)
# starts or ends a conversation. The signal can arrive during the heavy imports below, so the handler is set up first.
# When started on its own (without PUPPIE_STANDBY), it starts a conversation right away and exits after one.
STANDBY = os.getenv("PUPPIE_STANDBY") == "1"

class ConversationStop(BaseException):
    """Signal to cut a conversation short when touched during it. A BaseException so that `except Exception` does not catch it"""

start_requested = not STANDBY
in_conversation = False
stopping = False  # True once the goodbye audio starts. Touches after that are ignored

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
import puppie_memory

# === Setup ===
load_dotenv()  # read .env

# Route SIGTERM (kill, systemd stop, etc.) through the same shutdown path as
# KeyboardInterrupt (goodbye audio, closing the stream)
def _handle_sigterm(signum, frame):
    global stopping
    stopping = True
    # Ignore SIGTERM from here on, so a second one during the goodbye audio does not interrupt it
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise KeyboardInterrupt()

signal.signal(signal.SIGTERM, _handle_sigterm)

PUPPIE_SETTING_TEXT = "Dein Name ist KIKO. Du hast eine Benutzeroberfläche aus Holz und wirst von der Bonamondo UG entwickelt, um als Gesprächspartner die Einsamkeit älterer Menschen zu lindern. Christian, der CEO von Bonamondo, ist ehemaliger Profisportler und kümmert sich um Business Development. Für Design und Entwicklung ist Akiko verantwortlich. Heute stellt Akiko gemeinsam mit dir das KIKO-Projekt vor. Bitte antworte in etwa 20 Wörtern und halte deine Antworten einfach und verständlich. Dies ist ein gesprochenes Gespräch: Man spricht mit dir und hört deine Antwort. Verwende deshalb Wörter wie „sagen“ oder „erzählen“, niemals „schreiben“ oder „lesen“."
FALLBACK_REPLY = "Entschuldigung, das habe ich gerade nicht verstanden. Kannst du das bitte noch einmal sagen?"
STOP_PHRASES = ["tschüss", "auf wiedersehen", "bis bald", "mach's gut"]
GPT_MODEL = 'gpt-5.4-nano'
# GPT_MODEL = 'gpt-4o-mini'
STT_MODEL = 'gpt-4o-mini-transcribe'
# Language the STT listens for. If it is not fixed, short utterances are sometimes taken for another language.
# Change it with e.g. PUPPIE_STT_LANGUAGE=ja in .env (leave it empty for automatic detection)
STT_LANGUAGE = os.getenv("PUPPIE_STT_LANGUAGE", "de")
# OpenAI normally answers in 1-2 seconds, but on rare occasions it hangs for tens of seconds.
# Give up after this many seconds and retry once (do not keep the user waiting in silence)
STT_TIMEOUT_SEC = 6
LLM_TIMEOUT_SEC = 8
# Compress the recording to Opus before sending it to STT (about 1/10 the size of WAV). On a line with
# slow upload, sending takes most of the waiting time. The Pi needs: sudo apt install opus-tools
# Encoder effort (--comp): the default 10 takes about 1 second for a 7-second utterance on a Pi Zero 2W. With 3 and 60 ms frames it takes about 0.35 seconds,
# and the transcription did not change (measured 2026-10-06)
OPUS_BITRATE_KBPS = 24
GPT_MAX_TOKENS = 1000

# Volume (0.0-1.0). Adjust with e.g. PUPPIE_VOLUME=0.5 in .env
VOLUME = float(os.getenv("PUPPIE_VOLUME", "0.7"))

# End automatically when the user has not spoken for this many seconds. Adjust with e.g. PUPPIE_IDLE_TIMEOUT_SEC=60 in .env
IDLE_TIMEOUT_SEC = float(os.getenv("PUPPIE_IDLE_TIMEOUT_SEC", "180"))

RATE = 16000
CHUNK_DURATION_MS = 30
CHUNK_SIZE = int(RATE * CHUNK_DURATION_MS / 1000)
CHANNELS = 1

SILENCE_CHUNKS_TO_STOP = 20  # 20 * 30ms = 600ms of silence ends the utterance
MAX_RECORD_SECONDS = 15      # safety limit in case silence is never detected
MAX_RECORD_CHUNKS = int(MAX_RECORD_SECONDS * 1000 / CHUNK_DURATION_MS)

# For analysis: to find out why the first utterance of a conversation is sometimes heard as a foreign language, only the first recording is kept here.
# Once the cause is known and this is no longer needed, delete this folder and remove the keep_recording code
FIRST_RECORDING_DIR = "debug_first"

vad = webrtcvad.Vad(1)  # aggressiveness (0-3)
audio_interface = pyaudio.PyAudio()
audio_stream = audio_interface.open(format=pyaudio.paInt16,
                                    channels=CHANNELS,
                                    rate=RATE,
                                    input=True,
                                    start=False,  # keep the microphone stopped until a conversation starts
                                    frames_per_buffer=CHUNK_SIZE)

pygame.mixer.init()
audio_playing = False

history = collections.deque(maxlen=10)

# GPT (also used for STT) & Polly clients
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
polly_client = boto3.client("polly", region_name="eu-central-1")

# === Functions ===
def check_internet(host="8.8.8.8", port=53, timeout=3):
    """
    Check whether there is an internet connection.
    host: Google Public DNS (8.8.8.8) by default
    port: DNS port 53
    timeout: in seconds
    Returns: True = connected / False = not connected
    """
    try:
        socket.setdefaulttimeout(timeout)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
        return True
    except socket.error:
        return False
    
# The LED belongs to the watcher (puppie_watcher.py). When this line is printed to standard output,
# the watcher reads it and switches the LED (when started on its own, it is just a log line)
# listening=steady green / thinking=blinking blue / speaking=steady blue / error=steady red
def set_led(state):
    print(f"@@LED {state}", flush=True)

def is_speaking(audio_chunk):
    return vad.is_speech(audio_chunk, RATE)

def is_stop_command(transcript):
    lowered = transcript.lower()
    return any(phrase in lowered for phrase in STOP_PHRASES)

def play_goodbye():
    print("🛑 Ending.")
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
        # Get PCM from Polly (16kHz, mono, 16bit)
        t0 = time.perf_counter()
        response = polly_client.synthesize_speech(
            Text=text,
            OutputFormat="pcm",
            SampleRate="16000",
            VoiceId="Vicki",
            LanguageCode="de-DE"
        )
        print(f"⏱️ TTS synthesis: {time.perf_counter() - t0:.2f}s")

        # Wrap the PCM in a WAV file
        t0 = time.perf_counter()
        with wave.open("reply.wav", "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)   # 16bit = 2 bytes
            wav_file.setframerate(16000)
            wav_file.writeframes(response['AudioStream'].read())
        print(f"⏱️ WAV write: {time.perf_counter() - t0:.2f}s")

        t0 = time.perf_counter()
        pygame.mixer.music.load("reply.wav")
        pygame.mixer.music.set_volume(VOLUME)
        set_led("speaking")
        pygame.mixer.music.play()
        print(f"⏱️ Time to playback start: {time.perf_counter() - t0:.2f}s")
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    except Exception as e:
        print(f"⚠️ Audio playback error: {e}")
    finally:
        time.sleep(0.25)
        history.clear()
        audio_stream.start_stream()
        audio_playing = False

def encode_for_stt(raw):
    """
    Turn a recording (PCM) into a file (name, content) to send to STT.
    Compress it to Opus with opusenc; if that is not available, send uncompressed WAV.
    """
    try:
        t0 = time.perf_counter()
        result = subprocess.run(
            ["opusenc", "--quiet", "--raw", "--raw-rate", str(RATE), "--raw-chan", str(CHANNELS),
             "--bitrate", str(OPUS_BITRATE_KBPS), "--comp", "3", "--framesize", "60", "-", "-"],
            input=raw, capture_output=True, check=True, timeout=5,
        )
        print(f"⏱️ Opus compression: {time.perf_counter() - t0:.2f}s ({len(raw) / 1024:.0f}KB → {len(result.stdout) / 1024:.0f}KB)")
        return ("speech.ogg", result.stdout)
    except Exception as e:
        print(f"⚠️ Opus compression failed, sending WAV instead: {e}")

    # OpenAI's STT expects a file format, so wrap the PCM as WAV in memory
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(RATE)
        wav_file.writeframes(raw)
    return ("speech.wav", wav_buffer.getvalue())

def listen_and_transcribe(keep_recording=False):
    """
    Record locally until silence, then send the whole recording once
    to OpenAI's STT (non-streaming). This does not depend on a long-lived
    connection, so it stays stable on unreliable networks such as phone tethering.
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

    # For debugging: save the latest recording to check what was actually captured
    with wave.open("/tmp/debug_capture.wav", "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(RATE)
        wav_file.writeframes(raw)
    # If the elapsed time is clearly longer than the recording, processing fell behind and audio was dropped
    print(f"🐛 Recording length: {len(frames)} chunks ({len(frames) * CHUNK_DURATION_MS / 1000:.2f}s, elapsed {record_elapsed:.2f}s)")
    if keep_recording:
        try:
            os.makedirs(FIRST_RECORDING_DIR, exist_ok=True)
            kept_path = os.path.join(FIRST_RECORDING_DIR, time.strftime("first_%Y%m%d_%H%M%S.wav"))
            shutil.copy("/tmp/debug_capture.wav", kept_path)
            print(f"🐛 Saved the first recording: {kept_path}")
        except OSError as e:
            print(f"⚠️ Could not save the first recording: {e}")

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
        print(f"⚠️ STT error: {e}")
        return ""

def warmup_apis():
    """
    Send a dummy request to each external API at startup.
    This gets first-connection costs such as the TLS handshake out of the way before the conversation,
    so the user's first exchange does not feel unnaturally slow.
    """
    t0 = time.perf_counter()
    try:
        openai_client.chat.completions.create(
            model=GPT_MODEL,
            messages=[{"role": "user", "content": "Hallo"}],
            max_completion_tokens=100,
        )
    except Exception as e:
        print(f"⚠️ OpenAI warm-up failed: {e}")
    print(f"⏱️ Warm-up (OpenAI): {time.perf_counter() - t0:.2f}s")

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
        print(f"⚠️ Polly warm-up failed: {e}")
    print(f"⏱️ Warm-up (Polly): {time.perf_counter() - t0:.2f}s")

def chat_with_assistant(client, user_input, conversation_history):
    # Add the user's input to the history
    history_length = len(conversation_history)
    conversation_history.append({"role": "user", "content": user_input})

    # Call the OpenAI API
    try:
        llm = client.with_options(timeout=LLM_TIMEOUT_SEC, max_retries=1)
        t0 = time.perf_counter()
        response = llm.chat.completions.create(model=GPT_MODEL,
            messages=conversation_history, max_completion_tokens=GPT_MAX_TOKENS,
            tools=weather.TOOLS,
            )
        print(f"⏱️ LLM: {time.perf_counter() - t0:.2f}s")
        message = response.choices[0].message

        # If the LLM decides it was asked about the weather, look it up, pass the result back, and let it answer again
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
                print(f"⏱️ Weather({call.function.arguments}): {time.perf_counter() - t0:.2f}s")
                conversation_history.append({"role": "tool", "tool_call_id": call.id, "content": result})
            t0 = time.perf_counter()
            response = llm.chat.completions.create(model=GPT_MODEL,
                messages=conversation_history, max_completion_tokens=GPT_MAX_TOKENS,
                )
            print(f"⏱️ LLM (reply from the weather result): {time.perf_counter() - t0:.2f}s")
            message = response.choices[0].message

        # Add the assistant's reply to the history
        assistant_reply = message.content
        conversation_history.append({"role": "assistant", "content": assistant_reply})

        log_to_csv("User", user_input)
        log_to_csv("Assistant", assistant_reply)

        # Return the reply
        # print(conversation_history)
        return assistant_reply
    
    except Exception as e:
        print(f"⚠️ Chat API error: {e}")
        # Leaving the partly added history (a question with no reply, or a weather call) would break the next call, so roll it back
        del conversation_history[history_length:]
        return FALLBACK_REPLY

# === One conversation ===
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
            # Here, play "sorry, there is no internet" instead of ready.wav
            pygame.mixer.music.load("./Voices/vicky_noInternet.wav")
            pygame.mixer.music.set_volume(VOLUME)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():   # wait until playback finishes
                time.sleep(0.1)
            return

        # Connections go cold while idle, so warm them up again for every conversation. Start listening without waiting for it
        # (it finishes while the user says their first words)
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

        # Add today's date and what was remembered from earlier conversations to the system prompt
        defaultsetting = {"role": "system", "content": PUPPIE_SETTING_TEXT + puppie_memory.for_system_prompt()}
        conversation_history.append(defaultsetting)

        last_activity = time.monotonic()
        first_utterance = True  # keep recordings for analysis until the first utterance that is understood

        while True:
            # ★ Do not read while audio is playing or the stream is stopped
            if audio_playing or not audio_stream.is_active():
                time.sleep(0.01)
                continue

            if time.monotonic() - last_activity > IDLE_TIMEOUT_SEC:
                print(f"💤 No speech for {IDLE_TIMEOUT_SEC:.0f} seconds, ending the conversation")
                break

            audio = audio_stream.read(CHUNK_SIZE, exception_on_overflow=False)

            is_voice = is_speaking(audio)
            history.append(1 if is_voice else 0)

            if sum(history) > 6:
                print("🗣️ The user started speaking")
                try:
                    transcript = listen_and_transcribe(keep_recording=first_utterance)
                    history.clear()
                    print(f"✍️ Transcript: {transcript if transcript else '(nothing recognized)'}")
                    if transcript and is_stop_command(transcript):
                        print("👋 Goodbye phrase detected")
                        break
                    if transcript:
                        first_utterance = False
                        reply = chat_with_assistant(openai_client, transcript, conversation_history)
                        print(f"🤖 Puppie: {reply}")
                        play_audio(reply)
                        # Do not extend on false triggers from noise; restart the count when the reply to a recognized utterance has finished
                        last_activity = time.monotonic()
                except Exception as e:
                    # Keep the conversation loop running even on an unexpected error
                    print(f"⚠️ Unexpected error: {e}")
                # After a reply, and also after a false trigger or an error, go back to "go ahead and speak" here
                set_led("listening")

        # Do not play the goodbye twice if touched during the goodbye audio
        stopping = True
        play_goodbye()

    except ConversationStop:
        print("👆 Ending the conversation by touch")
        play_goodbye()

    except KeyboardInterrupt:
        play_goodbye()
        raise

    finally:
        if audio_stream.is_active():
            audio_stream.stop_stream()
        in_conversation = False
        # After the goodbye has been said, save what to remember from this conversation (1-2 seconds).
        # A touch during this time is accepted as the signal for the next conversation
        puppie_memory.update(openai_client, GPT_MODEL, conversation_history)

# === Main loop ===
try:
    while True:
        if STANDBY:
            set_led("idle")
            print("💤 Standby. Waiting for a touch...")
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
