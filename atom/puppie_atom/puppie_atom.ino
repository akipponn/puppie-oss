// Atom VoiceS3R: touch G8 (or press the button), speak, hear Puppie's reply from the speaker.
// The reply starts playing while the rest of it is still arriving.

#include <M5Unified.h>
#include <WiFi.h>
#include <NetworkClientSecure.h>
#include <Preferences.h>
#include <LittleFS.h>
#include "secrets.h"  // copy secrets.h.example to secrets.h and fill it in

static constexpr uint32_t SAMPLE_RATE = 16000;
static constexpr size_t RECORD_SECONDS = 10;  // the longest one recording can be
enum class RoundResult { Spoke, Quiet, Failed };  // up here so Arduino's auto prototypes see it
static constexpr size_t BLOCK_SAMPLES = 256;
static constexpr size_t TOTAL_SAMPLES = SAMPLE_RATE * RECORD_SECONDS;
static constexpr size_t PCM_BYTES = TOTAL_SAMPLES * sizeof(int16_t);
static constexpr size_t WAV_HEADER_BYTES = 44;
static constexpr uint32_t HTTP_TIMEOUT_MS = 30000;
static constexpr size_t SEND_CHUNK_BYTES = 4096;
static constexpr size_t WARM_UP_EXTRA_SAMPLES = SAMPLE_RATE / 5;
static constexpr uint32_t WARM_UP_LIMIT_MS = 5000;
static constexpr uint32_t TTS_SAMPLE_RATE = 24000;  // what the API sends for pcm
static constexpr size_t REPLY_AUDIO_BYTES = 2 * 1024 * 1024;  // about 40 seconds
static constexpr size_t READ_CHUNK_BYTES = 4096;
static constexpr uint8_t PLAY_VOLUME = 160;  // 0-255, 120 too quiet, 200 too loud (2026-11-05)
static constexpr size_t PLAY_PIECE_BYTES = TTS_SAMPLE_RATE / 4 * sizeof(int16_t);  // 0.25 seconds, the smallest piece
static constexpr size_t PLAY_MAX_PIECE_BYTES = TTS_SAMPLE_RATE * sizeof(int16_t);  // 1 second, when more has piled up
static constexpr int ATTEMPTS = 2;
// The speech API now and then sends nothing at all (seen from the Mac too); usually its first byte comes within 2.4 seconds.
static constexpr uint32_t TTS_FIRST_BYTE_MS = 5000;
static constexpr int TTS_ATTEMPTS = 3;

static const char API_HOST[] = "api.openai.com";
static const char GPT_MODEL[] = "gpt-5.4-nano";
// Only for the saved note (once per conversation): nano dropped old facts when merging (2026-11-22).
static const char MEMORY_MODEL[] = "gpt-5.4-mini";
static const char TTS_MODEL[] = "gpt-4o-mini-tts";
static const char TTS_VOICE[] = "marin";
static const char TTS_INSTRUCTIONS[] = "やさしく、あたたかい声で話してください。親しい友だちと話すような、明るい話し方で、テンポよく、ふつうの会話の速さで。";
// Japanese for now: German speech by a non-native speaker was often misheard.
static const char PUPPIE_SETTING_TEXT[] =
    "あなたの名前はぷっぴーです。親しみやすい日々の話し相手で、誰とでも楽しくおしゃべりします。"
    "40文字くらいで、簡単でわかりやすく答えてください。"
    "これは声での会話です。相手はあなたに話しかけ、あなたの答えを耳で聞きます。"
    "だから「書く」や「読む」という言葉は使わないでください。"
    "「私はこう言います」のような前置きなしに、すぐ答えてください。"
    "記号(＋、「」、*、#、かっこなど)や絵文字は使わず、声に出して自然な言葉だけで答えてください。"
    "いつも日本語で答えてください。";

static const char BOUNDARY[] = "puppieboundary";
static const char BODY_START[] =
    "--puppieboundary\r\n"
    "Content-Disposition: form-data; name=\"model\"\r\n\r\n"
    "gpt-4o-mini-transcribe\r\n"
    "--puppieboundary\r\n"
    "Content-Disposition: form-data; name=\"language\"\r\n\r\n"
    "ja\r\n"
    "--puppieboundary\r\n"
    "Content-Disposition: form-data; name=\"file\"; filename=\"speech.wav\"\r\n"
    "Content-Type: audio/wav\r\n\r\n";
static const char BODY_END[] = "\r\n--puppieboundary--\r\n";

static constexpr size_t START_BYTES = sizeof(BODY_START) - 1;
static constexpr size_t END_BYTES = sizeof(BODY_END) - 1;
static constexpr size_t BODY_BYTES = START_BYTES + WAV_HEADER_BYTES + PCM_BYTES + END_BYTES;  // room for the longest
static size_t recordedSamples = 0;  // how much of the room the last recording used

static uint8_t* body = nullptr;
static uint8_t* replyAudio = nullptr;
static uint32_t lastStatus = 0;
static NetworkClientSecure api;
static bool speakWhileReading = false;
static size_t queuedBytes = 0;
static uint32_t firstSoundAt = 0;
static size_t audioFilled = 0;     // bytes of the reply received so far
static int speakerGaps = 0;        // times the speaker ran empty in the middle of the reply
static bool speakerWasEmpty = false;
static bool interrupted = false;    // touched (or button) while Puppie was speaking: stop and listen
static uint32_t lastInterruptCheck = 0;

// The RGB LED Unit on the Grove port (3 LEDs, data on GPIO2) shows what Puppie is doing,
// with the same colours as the Pi version. A small task of its own keeps it fading even while
// the main loop waits for the network.
enum class LedState { Starting, Idle, Listening, Thinking, Speaking, Error };
static constexpr uint8_t LED_PIN = 2;
static constexpr int LED_COUNT = 3;
static constexpr uint8_t LED_BRIGHTNESS = 40;  // 0-255
static constexpr uint32_t LED_FADE_MS = 800;   // same as the Pi's PULSE_FADE_SEC
static constexpr uint32_t ERROR_SHOW_MS = 3000;
static volatile LedState ledState = LedState::Starting;

void setLed(LedState state) {
  ledState = state;
}

void writeLeds(uint8_t r, uint8_t g, uint8_t b) {
  static rmt_data_t data[LED_COUNT * 24];
  uint8_t colour[3] = {g, r, b};  // the LEDs expect green, red, blue
  int i = 0;
  for (int led = 0; led < LED_COUNT; led++) {
    for (int c = 0; c < 3; c++) {
      for (int bit = 7; bit >= 0; bit--) {
        bool one = colour[c] & (1 << bit);
        data[i].level0 = 1;
        data[i].duration0 = one ? 8 : 4;  // in 0.1 us
        data[i].level1 = 0;
        data[i].duration1 = one ? 4 : 8;
        i++;
      }
    }
  }
  rmtWrite(LED_PIN, data, LED_COUNT * 24, RMT_WAIT_FOR_EVER);
}

void ledTask(void*) {
  rmtInit(LED_PIN, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_1, 10000000);
  uint32_t lastColour = 0xFFFFFFFF;
  while (true) {
    LedState state = ledState;
    // 0..1..0 over two fades, for the pulsing states
    uint32_t t = millis() % (2 * LED_FADE_MS);
    uint8_t pulse = (t < LED_FADE_MS ? t : 2 * LED_FADE_MS - t) * LED_BRIGHTNESS / LED_FADE_MS;
    uint8_t r = 0, g = 0, b = 0;
    switch (state) {
      case LedState::Starting: r = g = b = pulse; break;
      case LedState::Listening: g = LED_BRIGHTNESS; break;
      case LedState::Thinking: b = pulse; break;
      case LedState::Speaking: b = LED_BRIGHTNESS; break;
      case LedState::Error: r = LED_BRIGHTNESS; break;
      case LedState::Idle: break;
    }
    uint32_t colour = (r << 16) | (g << 8) | b;
    if (colour != lastColour) {
      writeLeds(r, g, b);
      lastColour = colour;
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void put32(uint8_t* at, uint32_t value) { memcpy(at, &value, 4); }
void put16(uint8_t* at, uint16_t value) { memcpy(at, &value, 2); }

void writeWavHeader(uint8_t* wav, uint32_t pcmBytes) {
  memcpy(wav, "RIFF", 4);
  put32(wav + 4, 36 + pcmBytes);
  memcpy(wav + 8, "WAVEfmt ", 8);
  put32(wav + 16, 16);
  put16(wav + 20, 1);  // PCM
  put16(wav + 22, 1);  // mono
  put32(wav + 24, SAMPLE_RATE);
  put32(wav + 28, SAMPLE_RATE * sizeof(int16_t));
  put16(wav + 32, sizeof(int16_t));
  put16(wav + 34, 16);
  memcpy(wav + 36, "data", 4);
  put32(wav + 40, pcmBytes);
}

// The mic delivers only zeros (and then a click) for a while after it starts, so this runs before the button counts.
void warmUpMic() {
  static int16_t block[BLOCK_SAMPLES];
  uint32_t start = millis();
  M5.Mic.begin();
  bool heard = false;
  size_t afterSound = 0;
  while (afterSound < WARM_UP_EXTRA_SAMPLES && millis() - start < WARM_UP_LIMIT_MS) {
    M5.Mic.record(block, BLOCK_SAMPLES, SAMPLE_RATE);
    while (M5.Mic.isRecording() > 1) { M5.delay(1); }
    for (size_t i = 0; i < BLOCK_SAMPLES && !heard; i++) {
      heard = block[i] != 0;
    }
    if (heard) { afterSound += BLOCK_SAMPLES; }
  }
  while (M5.Mic.isRecording()) { M5.delay(1); }
}

// Listening ends on its own: when nobody starts speaking within WAIT_FOR_SPEECH_MS, or when
// SILENCE_AFTER_MS of quiet follows speech, or after RECORD_SECONDS. A 16 ms block counts as
// speech when its RMS is above SPEECH_LEVEL (room quiet was about 14, soft speech about 470).
static constexpr uint32_t SPEECH_LEVEL = 150;
static constexpr size_t WAIT_FOR_SPEECH_MS = 4000;
static constexpr size_t SILENCE_AFTER_MS = 800;
static bool endRequested = false;  // a touch or button press while listening ends the conversation
static bool forgetRequested = false;  // still touching FORGET_HOLD_MS after a touch: ask to forget the note
bool wasTouched();
bool heldLong();

uint32_t blockLevel(const int16_t* block, size_t count) {
  uint64_t sum = 0;
  for (size_t i = 0; i < count; i++) {
    int32_t s = block[i];
    sum += (uint64_t)(s * s);
  }
  return (uint32_t)sqrt((double)sum / count);
}

// Returns true when someone spoke. Afterwards body holds a WAV of just what was recorded.
bool record(int16_t* samples) {
  Serial.println("Recording...");
  static constexpr size_t MS_PER_BLOCK_X16 = BLOCK_SAMPLES * 1000 * 16 / SAMPLE_RATE;  // ms * 16
  size_t issued = 0;
  size_t checked = 0;
  bool heard = false;
  size_t quietBlocks = 0;
  uint32_t loudest = 0;
  while (issued < TOTAL_SAMPLES) {
    M5.Mic.record(&samples[issued], BLOCK_SAMPLES, SAMPLE_RATE);
    issued += BLOCK_SAMPLES;
    while (M5.Mic.isRecording() > 1) { M5.delay(1); }
    M5.update();
    if (wasTouched() || M5.BtnA.wasPressed()) {
      endRequested = true;
      break;
    }
    if (heldLong()) {
      forgetRequested = true;
      endRequested = true;
      break;
    }
    // Every block before the one still recording is finished now.
    for (; checked + BLOCK_SAMPLES < issued; checked += BLOCK_SAMPLES) {
      uint32_t level = blockLevel(&samples[checked], BLOCK_SAMPLES);
      if (level > loudest) { loudest = level; }
      if (level > SPEECH_LEVEL) {
        heard = true;
        quietBlocks = 0;
      } else {
        quietBlocks++;
      }
    }
    size_t checkedMs = checked / BLOCK_SAMPLES * MS_PER_BLOCK_X16 / 16;
    if (!heard && checkedMs >= WAIT_FOR_SPEECH_MS) { break; }
    if (heard && quietBlocks * MS_PER_BLOCK_X16 / 16 >= SILENCE_AFTER_MS) { break; }
  }
  while (M5.Mic.isRecording()) { M5.delay(1); }
  recordedSamples = issued;
  writeWavHeader(body + START_BYTES, recordedSamples * sizeof(int16_t));
  memcpy(body + START_BYTES + WAV_HEADER_BYTES + recordedSamples * sizeof(int16_t), BODY_END, END_BYTES);
  Serial.printf("RECORDED seconds=%.1f heard=%d loudest=%u\n", (float)issued / SAMPLE_RATE, heard ? 1 : 0, loudest);
  return heard;
}

// Reads the JSON string that follows "key": and undoes the escapes. Non-ASCII \uXXXX becomes UTF-8.
String jsonText(const String& json, const char* key) {
  String marker = String("\"") + key + "\"";
  int at = json.indexOf(marker);
  if (at < 0) { return ""; }
  at = json.indexOf(':', at + marker.length());
  if (at < 0) { return ""; }
  at = json.indexOf('"', at);
  if (at < 0) { return ""; }

  String text;
  for (int i = at + 1; i < (int)json.length(); i++) {
    char c = json[i];
    if (c == '"') { return text; }
    if (c != '\\') {
      text += c;
      continue;
    }
    char escaped = json[++i];
    if (escaped == 'n') {
      text += '\n';
    } else if (escaped == 'u') {
      uint32_t code = strtoul(json.substring(i + 1, i + 5).c_str(), nullptr, 16);
      i += 4;
      if (code < 0x80) {
        text += (char)code;
      } else if (code < 0x800) {
        text += (char)(0xC0 | (code >> 6));
        text += (char)(0x80 | (code & 0x3F));
      } else {
        text += (char)(0xE0 | (code >> 12));
        text += (char)(0x80 | ((code >> 6) & 0x3F));
        text += (char)(0x80 | (code & 0x3F));
      }
    } else {
      text += escaped;
    }
  }
  return "";
}

String jsonEscaped(const String& text) {
  String out;
  for (size_t i = 0; i < text.length(); i++) {
    char c = text[i];
    if (c == '"' || c == '\\') {
      out += '\\';
      out += c;
    } else if (c == '\n') {
      out += "\\n";
    } else if ((uint8_t)c >= 0x20) {
      out += c;
    }
  }
  return out;
}

// Returns true when the existing connection was kept; connectMs is how long a new one took.
bool ensureConnected(bool& ok, uint32_t& connectMs) {
  connectMs = 0;
  if (api.connected()) {
    ok = true;
    return true;
  }
  api.stop();
  uint32_t start = millis();
  ok = api.connect(API_HOST, 443);
  connectMs = millis() - start;
  return false;
}

void feedSpeaker(size_t filled, bool atEnd);

int readByteBy(uint32_t deadline) {
  while ((int32_t)(deadline - millis()) > 0) {
    if (api.available()) { return api.read(); }
    if (!api.connected()) { return -1; }
    if (speakWhileReading) { feedSpeaker(audioFilled, false); }
    delay(1);
  }
  return -1;
}

bool readLineBy(uint32_t deadline, String& line) {
  line = "";
  while (true) {
    int c = readByteBy(deadline);
    if (c < 0) { return false; }
    if (c == '\n') {
      line.trim();
      return true;
    }
    line += (char)c;
  }
}

bool readBytesBy(uint32_t deadline, size_t count, String& out) {
  for (size_t i = 0; i < count; i++) {
    int c = readByteBy(deadline);
    if (c < 0) { return false; }
    out += (char)c;
  }
  return true;
}

// Reads one whole HTTP response and leaves the connection ready for the next request.
bool readResponse(int& code, String& content) {
  uint32_t deadline = millis() + HTTP_TIMEOUT_MS;
  String line;
  if (!readLineBy(deadline, line)) { return false; }
  code = line.substring(9, 12).toInt();

  long contentLength = -1;
  bool chunked = false;
  bool closeAfter = false;
  while (true) {
    if (!readLineBy(deadline, line)) { return false; }
    if (line.length() == 0) { break; }
    String lower = line;
    lower.toLowerCase();
    if (lower.startsWith("content-length:")) { contentLength = lower.substring(15).toInt(); }
    if (lower.startsWith("transfer-encoding:") && lower.indexOf("chunked") >= 0) { chunked = true; }
    if (lower.startsWith("connection:") && lower.indexOf("close") >= 0) { closeAfter = true; }
  }

  content = "";
  if (chunked) {
    while (true) {
      if (!readLineBy(deadline, line)) { return false; }
      size_t size = strtoul(line.c_str(), nullptr, 16);
      if (size == 0) {
        readLineBy(deadline, line);
        break;
      }
      if (!readBytesBy(deadline, size, content)) { return false; }
      if (!readLineBy(deadline, line)) { return false; }
    }
  } else if (contentLength >= 0) {
    if (!readBytesBy(deadline, contentLength, content)) { return false; }
  } else {
    return false;
  }
  if (closeAfter) { api.stop(); }
  return true;
}

// The mic and the speaker share the audio chip, so the mic stops while Puppie speaks.
void startSpeaker() {
  M5.Mic.end();
  M5.Speaker.begin();
  M5.Speaker.setVolume(PLAY_VOLUME);
  queuedBytes = 0;
  firstSoundAt = 0;
  audioFilled = 0;
  speakerGaps = 0;
  speakerWasEmpty = false;
  interrupted = false;
}

// While Puppie speaks, a touch or the button stops the voice; the conversation goes on with listening.
// To end the conversation, touch once more while it listens.
bool checkInterrupt() {
  if (interrupted || firstSoundAt == 0) { return interrupted; }
  if (millis() - lastInterruptCheck < 50) { return false; }
  lastInterruptCheck = millis();
  M5.update();
  bool touched = wasTouched();
  if (touched || M5.BtnA.wasPressed()) {
    interrupted = true;
    M5.Speaker.stop();
    Serial.printf("INTERRUPTED by=%s at=%.2fs\n", touched ? "touch" : "button", (millis() - firstSoundAt) / 1000.0);
  }
  return interrupted;
}

// Hands what has arrived to the speaker: at least 0.25 seconds at a time, up to 1 second when more has piled up.
// The speaker holds at most two pieces at a time. It is also called while waiting for data, so the speaker
// never runs dry while sound is already here. At the end the last, shorter piece goes too.
// replyAudio is never overwritten, so the pieces can point into it.
void feedSpeaker(size_t filled, bool atEnd) {
  if (checkInterrupt()) { return; }
  if (queuedBytes > 0 && !speakerWasEmpty && M5.Speaker.isPlaying(0) == 0) {
    speakerWasEmpty = true;
    speakerGaps++;
    Serial.printf("SPEAKER_GAP at=%.2fs waiting=%u\n", queuedBytes / 2.0 / TTS_SAMPLE_RATE, (unsigned)(filled - queuedBytes));
  }
  while (true) {
    size_t ready = (filled - queuedBytes) & ~(size_t)1;
    if (ready == 0 || (ready < PLAY_PIECE_BYTES && !atEnd)) { return; }
    if (M5.Speaker.isPlaying(0) >= 2) {
      if (!atEnd || checkInterrupt()) { return; }
      M5.delay(1);
      continue;
    }
    size_t piece = min(ready, PLAY_MAX_PIECE_BYTES);
    M5.Speaker.playRaw((const int16_t*)(replyAudio + queuedBytes), piece / 2, TTS_SAMPLE_RATE, false, 1, 0);
    if (firstSoundAt == 0) {
      firstSoundAt = millis();
      Serial.println("SOUND_START");
      setLed(LedState::Speaking);
    }
    queuedBytes += piece;
    speakerWasEmpty = false;
  }
}

bool readAudioBy(uint32_t deadline, size_t count, size_t& filled) {
  size_t left = count;
  while (left > 0) {
    if ((int32_t)(deadline - millis()) <= 0) { return false; }
    if (interrupted) { return false; }  // the rest of the reply is not wanted; speak() drops the connection
    int ready = api.available();
    if (ready <= 0) {
      if (!api.connected()) { return false; }
      if (speakWhileReading) { feedSpeaker(filled, false); }
      delay(1);
      continue;
    }
    if (filled >= REPLY_AUDIO_BYTES) {
      // More than the buffer holds: read it away and keep what fits
      uint8_t discard[READ_CHUNK_BYTES];
      int got = api.read(discard, min({left, (size_t)ready, READ_CHUNK_BYTES}));
      if (got > 0) { left -= got; }
      continue;
    }
    size_t want = min({left, (size_t)ready, READ_CHUNK_BYTES, REPLY_AUDIO_BYTES - filled});
    int got = api.read(replyAudio + filled, want);
    if (got > 0) {
      filled += got;
      audioFilled = filled;
      left -= got;
      if (speakWhileReading) { feedSpeaker(filled, false); }
    }
  }
  return true;
}

// Reads one response whose body is audio into replyAudio. firstByteMs is the wait for the start of the reply.
bool readAudioResponse(int& code, size_t& filled, uint32_t& firstByteMs) {
  uint32_t start = millis();
  uint32_t deadline = start + HTTP_TIMEOUT_MS;
  String line;
  if (!readLineBy(start + TTS_FIRST_BYTE_MS, line)) { return false; }
  firstByteMs = millis() - start;
  code = line.substring(9, 12).toInt();
  speakWhileReading = code == 200;
  if (speakWhileReading) { startSpeaker(); }

  long contentLength = -1;
  bool chunked = false;
  bool closeAfter = false;
  while (true) {
    if (!readLineBy(deadline, line)) { return false; }
    if (line.length() == 0) { break; }
    String lower = line;
    lower.toLowerCase();
    if (lower.startsWith("content-length:")) { contentLength = lower.substring(15).toInt(); }
    if (lower.startsWith("transfer-encoding:") && lower.indexOf("chunked") >= 0) { chunked = true; }
    if (lower.startsWith("connection:") && lower.indexOf("close") >= 0) { closeAfter = true; }
  }

  filled = 0;
  if (chunked) {
    while (true) {
      if (!readLineBy(deadline, line)) { return false; }
      size_t size = strtoul(line.c_str(), nullptr, 16);
      if (size == 0) {
        readLineBy(deadline, line);
        break;
      }
      if (!readAudioBy(deadline, size, filled)) { return false; }
      if (!readLineBy(deadline, line)) { return false; }
    }
  } else if (contentLength >= 0) {
    if (!readAudioBy(deadline, contentLength, filled)) { return false; }
  } else {
    return false;
  }
  if (closeAfter) { api.stop(); }
  Serial.printf("TTS_RECEIVED bytes=%u queued=%u\n", (unsigned)filled, (unsigned)queuedBytes);
  if (speakWhileReading) { feedSpeaker(filled, true); }
  Serial.printf("TTS_ALL_QUEUED queued=%u\n", (unsigned)queuedBytes);
  return true;
}

size_t bodyBytes() { return START_BYTES + WAV_HEADER_BYTES + recordedSamples * sizeof(int16_t) + END_BYTES; }

bool sendAll() {
  size_t sent = 0;
  uint32_t lastProgress = millis();
  size_t total = bodyBytes();
  while (sent < total) {
    size_t count = min(SEND_CHUNK_BYTES, total - sent);
    size_t written = api.write(body + sent, count);
    if (written > 0) {
      sent += written;
      lastProgress = millis();
    } else if (millis() - lastProgress > HTTP_TIMEOUT_MS || !api.connected()) {
      return false;
    }
  }
  return true;
}

String transcribe() {
  uint32_t start = millis();
  for (int attempt = 1; attempt <= ATTEMPTS; attempt++) {
    bool ok = false;
    uint32_t connectMs = 0;
    bool reused = ensureConnected(ok, connectMs);
    if (!ok) { continue; }

    uint32_t sendStart = millis();
    api.printf("POST /v1/audio/transcriptions HTTP/1.1\r\n"
               "Host: %s\r\n"
               "Authorization: Bearer %s\r\n"
               "Content-Type: multipart/form-data; boundary=%s\r\n"
               "Content-Length: %u\r\n\r\n",
               API_HOST, OPENAI_API_KEY, BOUNDARY, (unsigned)bodyBytes());
    if (!sendAll()) {
      api.stop();
      continue;
    }
    uint32_t uploaded = millis();

    int code = 0;
    String content;
    if (!readResponse(code, content)) {
      api.stop();
      continue;
    }
    String text = jsonText(content, "text");
    Serial.printf("STT_RESULT ms=%lu connect=%lu send=%lu wait=%lu reused=%d attempt=%d code=%d %s\n", millis() - start,
                  connectMs, uploaded - sendStart, millis() - uploaded, reused ? 1 : 0, attempt, code, text.c_str());
    return text;
  }
  Serial.printf("STT failed after %lu ms\n", millis() - start);
  return "";
}

// The flow of the conversation: the last HISTORY_TURNS exchanges are sent along, so a follow-up
// like "and okra?" is understood. Kept in RAM for one conversation only, cleared when it ends.
static constexpr int HISTORY_TURNS = 6;
static String historyUser[HISTORY_TURNS];
static String historyReply[HISTORY_TURNS];
static int historyCount = 0;

void clearHistory() {
  for (int i = 0; i < HISTORY_TURNS; i++) {
    historyUser[i] = "";
    historyReply[i] = "";
  }
  historyCount = 0;
}

void rememberExchange(const String& userText, const String& reply) {
  if (historyCount == HISTORY_TURNS) {
    for (int i = 1; i < HISTORY_TURNS; i++) {
      historyUser[i - 1] = historyUser[i];
      historyReply[i - 1] = historyReply[i];
    }
    historyCount--;
  }
  historyUser[historyCount] = userText;
  historyReply[historyCount] = reply;
  historyCount++;
}

// Weather, like the Pi's weather.py: Open-Meteo, no API key needed. The Pi version lets the LLM ask for
// it (function calling); here, to keep it small, a weather word in what was said fetches the forecast
// for the home town first, and it goes along with the question.
static const char HOME_CITY[] = "ライプツィヒ";
static constexpr float HOME_LATITUDE = 51.34f;
static constexpr float HOME_LONGITUDE = 12.37f;
static const char WEATHER_HOST[] = "api.open-meteo.com";
static constexpr uint32_t WEATHER_TIMEOUT_MS = 5000;
static const char* const WEATHER_WORDS[] = {"天気", "気温", "雨", "傘", "晴れ", "雪", "寒い", "暑い", "何度", "予報"};

bool asksAboutWeather(const String& text) {
  for (const char* word : WEATHER_WORDS) {
    if (text.indexOf(word) >= 0) { return true; }
  }
  return false;
}

// WMO weather code → Japanese for speaking
const char* weatherName(int code) {
  switch (code) {
    case 0: return "快晴";
    case 1: return "晴れ";
    case 2: return "晴れときどきくもり";
    case 3: return "くもり";
    case 45: case 48: return "霧";
    case 51: case 53: case 55: return "霧雨";
    case 56: case 57: return "凍る霧雨";
    case 61: return "小雨";
    case 63: return "雨";
    case 65: return "強い雨";
    case 66: case 67: return "凍る雨";
    case 71: return "小雪";
    case 73: case 77: return "雪";
    case 75: return "大雪";
    case 80: case 81: return "にわか雨";
    case 82: return "激しいにわか雨";
    case 85: case 86: return "にわか雪";
    case 95: return "雷雨";
    case 96: case 99: return "ひょうを伴う雷雨";
    default: return "よくわからない天気";
  }
}

// The {...} after "name": (Open-Meteo's sections have nothing nested). "current_units" etc. are skipped.
String jsonSection(const String& json, const char* name) {
  String marker = String("\"") + name + "\":{";
  int at = json.indexOf(marker);
  if (at < 0) { return ""; }
  int end = json.indexOf('}', at);
  return end < 0 ? "" : json.substring(at + marker.length(), end);
}

// The raw value after "key": inside a section; for a list, item number index of it.
String jsonValue(const String& section, const char* key, int index) {
  String marker = String("\"") + key + "\":";
  int at = section.indexOf(marker);
  if (at < 0) { return ""; }
  at += marker.length();
  if (index >= 0) {
    if (section[at] != '[') { return ""; }
    at++;
    for (int i = 0; i < index; i++) {
      at = section.indexOf(',', at);
      if (at < 0) { return ""; }
      at++;
    }
  }
  int end = at;
  while (end < (int)section.length() && section[end] != ',' && section[end] != ']' && section[end] != '}') { end++; }
  String value = section.substring(at, end);
  value.trim();
  return value;
}

// Returns the forecast as Japanese text for the LLM, or "" when it could not be fetched.
String fetchWeather() {
  uint32_t start = millis();
  NetworkClientSecure client;
  client.useBuiltinCACertBundle();
  if (!client.connect(WEATHER_HOST, 443)) {
    Serial.printf("WEATHER_ERROR connect ms=%lu\n", millis() - start);
    return "";
  }
  // HTTP/1.0: the answer comes in one piece (no chunks) and the connection closes at its end.
  client.printf("GET /v1/forecast?latitude=%.2f&longitude=%.2f&current=temperature_2m,weather_code"
                "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                "&timezone=auto&forecast_days=3 HTTP/1.0\r\n"
                "Host: %s\r\n\r\n",
                HOME_LATITUDE, HOME_LONGITUDE, WEATHER_HOST);
  String response;
  char buffer[512];
  uint32_t deadline = millis() + WEATHER_TIMEOUT_MS;
  while ((client.connected() || client.available()) && (int32_t)(deadline - millis()) > 0) {
    int count = client.read((uint8_t*)buffer, sizeof(buffer));
    if (count > 0) {
      response.concat(buffer, count);
    } else {
      delay(1);
    }
  }
  client.stop();
  int bodyAt = response.indexOf("\r\n\r\n");
  if (!response.startsWith("HTTP/1.") || response.substring(9, 12) != "200" || bodyAt < 0) {
    Serial.printf("WEATHER_ERROR ms=%lu %.80s\n", millis() - start, response.c_str());
    return "";
  }
  String json = response.substring(bodyAt + 4);

  String current = jsonSection(json, "current");
  String daily = jsonSection(json, "daily");
  if (jsonValue(current, "temperature_2m", -1).length() == 0 || jsonValue(daily, "weather_code", 0).length() == 0) {
    Serial.printf("WEATHER_ERROR parse %.120s\n", json.c_str());
    return "";
  }
  String text = String(HOME_CITY) + "の天気。今: " + String((int)round(jsonValue(current, "temperature_2m", -1).toFloat())) +
                "度、" + weatherName(jsonValue(current, "weather_code", -1).toInt()) + "。";
  const char* days[] = {"今日", "明日", "あさって"};
  for (int i = 0; i < 3; i++) {
    String code = jsonValue(daily, "weather_code", i);
    if (code.length() == 0) { break; }
    text += String(days[i]) + ": " + weatherName(code.toInt()) + "、最低" +
            String((int)round(jsonValue(daily, "temperature_2m_min", i).toFloat())) + "度、最高" +
            String((int)round(jsonValue(daily, "temperature_2m_max", i).toFloat())) + "度";
    String rain = jsonValue(daily, "precipitation_probability_max", i);
    if (rain.length() > 0 && rain != "null") { text += "、雨の確率" + rain + "パーセント"; }
    text += "。";
  }
  Serial.printf("WEATHER ms=%lu %s\n", millis() - start, text.c_str());
  return text;
}

// The saved note (see updateMemory below), read from flash when a conversation starts.
static String conversationMemory;
static const char MEMORY_NOTE_INTRO[] =
    "前に話したときのメモです。役に立つときだけ、自然に使ってください。メモがあることは言わなくてかまいません。\n";

String askLlm(const String& userText, bool withWeather) {
  String request = String("{\"model\":\"") + GPT_MODEL + "\",\"max_completion_tokens\":1000,\"messages\":["
                   "{\"role\":\"system\",\"content\":\"" + jsonEscaped(PUPPIE_SETTING_TEXT) + "\"},";
  if (conversationMemory.length() > 0) {
    request += "{\"role\":\"system\",\"content\":\"" + jsonEscaped(String(MEMORY_NOTE_INTRO) + conversationMemory) + "\"},";
  }
  if (withWeather) {
    String weather = fetchWeather();
    String note = weather.length() > 0
        ? String("天気を聞かれたら、この予報をもとに短く答えてください。町を言われなければ") + HOME_CITY +
              "のことです。ほかの町の天気はわからないと伝えてください。\n" + weather
        : String("天気のデータが今とれませんでした。天気を聞かれたら、今はわからないと伝えてください。");
    request += "{\"role\":\"system\",\"content\":\"" + jsonEscaped(note) + "\"},";
  }
  for (int i = 0; i < historyCount; i++) {
    request += "{\"role\":\"user\",\"content\":\"" + jsonEscaped(historyUser[i]) + "\"},"
               "{\"role\":\"assistant\",\"content\":\"" + jsonEscaped(historyReply[i]) + "\"},";
  }
  request += "{\"role\":\"user\",\"content\":\"" + jsonEscaped(userText) + "\"}]}";
  String reply = postChat(request, "LLM");
  if (reply.length() > 0) { rememberExchange(userText, reply); }
  return reply;
}

// Sends a chat request and returns the answer ("" on failure). label goes in front of the log lines.
String postChat(const String& request, const char* label) {
  uint32_t start = millis();
  for (int attempt = 1; attempt <= ATTEMPTS; attempt++) {
    bool ok = false;
    uint32_t connectMs = 0;
    bool reused = ensureConnected(ok, connectMs);
    if (!ok) { continue; }

    api.printf("POST /v1/chat/completions HTTP/1.1\r\n"
               "Host: %s\r\n"
               "Authorization: Bearer %s\r\n"
               "Content-Type: application/json\r\n"
               "Content-Length: %u\r\n\r\n",
               API_HOST, OPENAI_API_KEY, (unsigned)request.length());
    api.print(request);
    uint32_t sent = millis();

    int code = 0;
    String content;
    if (!readResponse(code, content)) {
      api.stop();
      continue;
    }
    String reply = code == 200 ? jsonText(content, "content") : "";
    Serial.printf("%s_RESULT ms=%lu connect=%lu wait=%lu reused=%d attempt=%d code=%d history=%d %s\n", label,
                  millis() - start, connectMs, millis() - sent, reused ? 1 : 0, attempt, code, historyCount,
                  reply.c_str());
    return reply;
  }
  Serial.printf("%s failed after %lu ms\n", label, millis() - start);
  return "";
}

// Memory: when a conversation ends, the LLM merges what was said into the note it already had, and the
// new note is saved in flash (Preferences), so it is still there after the power was off.
// The note covers the exchanges still in the history (the last HISTORY_TURNS).
// askLlm sends the note along with each request, so Puppie can use it (M3).
static const char MEMORY_PROMPT[] =
    "あなたはPuppieの記憶係です。「前のメモ」と「今日の会話」から、Puppieと話す人について覚えておく事実だけを、"
    "日本語の短い箇条書き（1行1つ、「- 」で始める）で書いてください。"
    "書くのは、その人が自分について話したことだけです（名前、食べないもの、好きなもの、家族、予定、気になっていることなど）。"
    "その人の言葉どおり、言い切りで書いてください。「〜かも」「可能性が高い」のようにぼかさないでください。"
    "例外や条件を話していたら、それも書いてください（例：「外では食べることもある」）。"
    "Puppieが言ったことや提案したこと、あいさつやお礼、会話の流れ、その人が試しに聞いた質問は書かないでください。"
    "天気の数字のようにすぐ古くなることや、もう終わった予定は消してください。同じことは一度だけ。"
    "前のメモの事実は、今日の会話で変わった、または終わったとはっきりわかるときだけ書き直し、それ以外はそのまま残してください。"
    "新しい話が前の事実の例外や条件なら、消さずに一行にまとめてください（例：「ふだんはお肉を食べない。外食では食べることもある」）。"
    "全部で300文字以内。覚えることがなければ前のメモをそのまま返してください。箇条書きだけを返してください。";
static constexpr size_t MEMORY_MAX_BYTES = 2000;  // a flash string can hold about 4000 bytes
static Preferences memoryStore;

String loadMemory() {
  memoryStore.begin("puppie", true);
  String memory = memoryStore.getString("memory", "");
  memoryStore.end();
  return memory;
}

void saveMemory(const String& memory) {
  memoryStore.begin("puppie", false);
  size_t written = memoryStore.putString("memory", memory);
  memoryStore.end();
  Serial.printf("MEMORY_SAVED bytes=%u ok=%d\n", (unsigned)memory.length(), written > 0 ? 1 : 0);
}

void updateMemory() {
  if (historyCount == 0) { return; }
  String old = loadMemory();
  Serial.printf("MEMORY_BEFORE bytes=%u %s\n", (unsigned)old.length(), old.c_str());
  String input = String("前のメモ: ") + (old.length() > 0 ? old : "（まだありません）") + "\n\n今日の会話:\n";
  for (int i = 0; i < historyCount; i++) {
    input += String("人: ") + historyUser[i] + "\nPuppie: " + historyReply[i] + "\n";
  }
  String request = String("{\"model\":\"") + MEMORY_MODEL + "\",\"max_completion_tokens\":2000,\"messages\":["
                   "{\"role\":\"system\",\"content\":\"" + jsonEscaped(MEMORY_PROMPT) + "\"},"
                   "{\"role\":\"user\",\"content\":\"" + jsonEscaped(input) + "\"}]}";
  String memory = postChat(request, "MEMORY");
  memory.trim();
  if (memory.length() == 0) { return; }  // keep the old note if the LLM did not answer
  if (memory.length() > MEMORY_MAX_BYTES) {
    Serial.printf("MEMORY_TOO_LONG bytes=%u, old note kept\n", (unsigned)memory.length());
    return;
  }
  saveMemory(memory);
}

void clearMemory() {
  memoryStore.begin("puppie", false);
  memoryStore.remove("memory");
  memoryStore.end();
  Serial.println("MEMORY_FORGOTTEN");
}

// Emoji are 4-byte UTF-8 characters; they should not be read aloud.
String withoutEmoji(const String& text) {
  String out;
  for (size_t i = 0; i < text.length(); i++) {
    if (((uint8_t)text[i] & 0xF8) == 0xF0) {
      i += 3;
    } else {
      out += text[i];
    }
  }
  out.trim();
  return out;
}

size_t speak(const String& text) {
  String request = String("{\"model\":\"") + TTS_MODEL + "\",\"voice\":\"" + TTS_VOICE +
                   "\",\"instructions\":\"" + jsonEscaped(TTS_INSTRUCTIONS) +
                   "\",\"response_format\":\"pcm\",\"input\":\"" + jsonEscaped(text) + "\"}";
  uint32_t start = millis();
  for (int attempt = 1; attempt <= TTS_ATTEMPTS; attempt++) {
    bool ok = false;
    uint32_t connectMs = 0;
    bool reused = ensureConnected(ok, connectMs);
    if (!ok) { continue; }

    api.printf("POST /v1/audio/speech HTTP/1.1\r\n"
               "Host: %s\r\n"
               "Authorization: Bearer %s\r\n"
               "Content-Type: application/json\r\n"
               "Content-Length: %u\r\n\r\n",
               API_HOST, OPENAI_API_KEY, (unsigned)request.length());
    api.print(request);

    int code = 0;
    size_t filled = 0;
    uint32_t firstByteMs = 0;
    if (!readAudioResponse(code, filled, firstByteMs)) {
      api.stop();
      speakWhileReading = false;
      if (interrupted) {
        Serial.printf("TTS_INTERRUPTED ms=%lu bytes=%u\n", millis() - start, (unsigned)filled);
        return max(filled, (size_t)1);  // it did speak; do not try again
      }
      continue;
    }
    speakWhileReading = false;
    Serial.printf("TTS_RESULT ms=%lu connect=%lu first_byte=%lu reused=%d attempt=%d code=%d bytes=%u seconds=%.1f\n",
                  millis() - start, connectMs, firstByteMs, reused ? 1 : 0, attempt, code, (unsigned)filled,
                  filled / 2.0 / TTS_SAMPLE_RATE);
    if (code != 200) {
      Serial.printf("TTS_ERROR %.*s\n", (int)min(filled, (size_t)300), (const char*)replyAudio);
      return 0;
    }
    return filled;
  }
  Serial.printf("TTS failed after %lu ms\n", millis() - start);
  return 0;
}

void finishSpeaking() {
  while (M5.Speaker.isPlaying() && !checkInterrupt()) { M5.delay(1); }
  if (interrupted) { M5.Speaker.stop(); }
  Serial.printf("PLAY_DONE gaps=%d\n", speakerGaps);
  M5.Speaker.end();
  warmUpMic();
  Serial.println("MIC_READY");
}

// Fixed sentences are made by TTS only the first time, then kept as files in flash (LittleFS) and played from
// there: no waiting, no internet. The file name comes from the text, voice and instructions, so a changed
// sentence is made again. Erasing the flash removes them; they are made again when needed.
static bool phraseFilesOk = false;

String phrasePath(const char* text) {
  uint32_t hash = 5381;
  for (const char* part : {text, TTS_VOICE, TTS_INSTRUCTIONS, TTS_MODEL}) {
    for (const char* c = part; *c; c++) { hash = hash * 33 + (uint8_t)*c; }
  }
  char path[24];
  snprintf(path, sizeof(path), "/phrase_%08lx.pcm", (unsigned long)hash);
  return path;
}

bool playPhraseFile(const String& path) {
  if (!phraseFilesOk || !LittleFS.exists(path)) { return false; }
  File file = LittleFS.open(path, "r");
  size_t size = file ? file.size() : 0;
  size_t bytes = size > 0 && size <= REPLY_AUDIO_BYTES ? file.read(replyAudio, size) : 0;
  if (file) { file.close(); }
  if (bytes == 0 || bytes != size) {
    Serial.printf("PHRASE_FILE_BAD %s bytes=%u\n", path.c_str(), (unsigned)size);
    LittleFS.remove(path);
    return false;
  }
  Serial.printf("PHRASE_FROM_FLASH %s bytes=%u\n", path.c_str(), (unsigned)bytes);
  startSpeaker();
  feedSpeaker(bytes & ~(size_t)1, true);
  finishSpeaking();
  return true;
}

void savePhraseFile(const String& path, size_t bytes) {
  if (!phraseFilesOk) { return; }
  uint32_t start = millis();
  File file = LittleFS.open(path, "w");
  size_t written = file ? file.write(replyAudio, bytes) : 0;
  if (file) { file.close(); }
  if (written != bytes) {
    LittleFS.remove(path);  // never keep half a sentence
  }
  Serial.printf("PHRASE_SAVED %s bytes=%u ok=%d ms=%lu\n", path.c_str(), (unsigned)bytes, written == bytes ? 1 : 0,
                millis() - start);
}

// Says a fixed sentence. False if it could not be said.
bool say(const char* text) {
  String path = phrasePath(text);
  if (playPhraseFile(path)) { return true; }
  setLed(LedState::Thinking);
  size_t bytes = speak(text);
  if (M5.Speaker.isRunning()) { finishSpeaking(); }
  if (bytes > 1 && !interrupted) { savePhraseFile(path, bytes); }  // only a sentence that arrived whole
  return bytes > 0;
}

// Forgetting (M4): after a touch held for FORGET_HOLD_MS, Puppie asks first. A touch within FORGET_WAIT_MS
// (or during the question) erases the note; otherwise the note is kept. True if the note was erased.
static constexpr uint32_t FORGET_WAIT_MS = 10000;

// Fixed sentences in the language set by PUPPIE_LANGUAGE in secrets.h ("de", "en" or "ja"; German if not set).
#ifndef PUPPIE_LANGUAGE
#define PUPPIE_LANGUAGE "de"
#endif
struct Phrases {
  const char* forgetQuestion;
  const char* forgetDone;
  const char* forgetKept;
};
static const Phrases PHRASES_DE = {
    "Soll ich alles vergessen, was ich mir gemerkt habe? Wenn ja, berühre mich noch einmal. "
    "Wenn du mich 10 Sekunden nicht berührst, lasse ich es.",
    "Okay, ich habe alles vergessen.",
    "Gut, dann behalte ich es."};
static const Phrases PHRASES_EN = {
    "Shall I forget everything I remember about you? If yes, touch me once more. "
    "If you don't touch me for 10 seconds, I'll leave it.",
    "Okay, I have forgotten everything.",
    "All right, I'll keep it."};
static const Phrases PHRASES_JA = {
    "わたしが覚えていることを、全部消してもいい？いいなら、もう一度タッチしてね。10秒タッチがなかったら、やめておくね。",
    "わかった。全部忘れたよ。",
    "じゃあ、このまま覚えておくね。"};

static const Phrases* const PHRASES = strcmp(PUPPIE_LANGUAGE, "ja") == 0   ? &PHRASES_JA
                                      : strcmp(PUPPIE_LANGUAGE, "en") == 0 ? &PHRASES_EN
                                                                           : &PHRASES_DE;

bool askToForget() {
  Serial.println("FORGET_ASKED");
  if (!say(PHRASES->forgetQuestion)) {
    setLed(LedState::Error);
    M5.delay(ERROR_SHOW_MS);
    return false;
  }
  bool yes = interrupted;  // touched while the question was still playing
  setLed(LedState::Listening);
  uint32_t until = millis() + FORGET_WAIT_MS;
  while (!yes && millis() < until) {
    M5.update();
    if (wasTouched() || M5.BtnA.wasPressed()) { yes = true; }
    M5.delay(10);
  }
  Serial.printf("FORGET_ANSWER yes=%d\n", yes ? 1 : 0);
  if (!yes) {
    say(PHRASES->forgetKept);
    return false;
  }
  clearMemory();
  say(PHRASES->forgetDone);
  return true;
}

// Built-in capacitive touch on G8 (copper tape behind the wood). On ESP32-S3 the value
// goes UP when touched. The baseline slowly follows the untouched value; a touch is
// 3 readings in a row above baseline + TOUCH_RISE_PERCENT.
static constexpr uint8_t TOUCH_PIN = 8;
static constexpr uint32_t TOUCH_RISE_PERCENT = 12;
static constexpr int TOUCH_NEEDED = 3;
static constexpr uint32_t TOUCH_READ_MS = 50;
static uint32_t touchBaseline = 0;
static int touchCount = 0;
static bool touchHeld = false;
static uint32_t touchStartedAt = 0;  // when the finger still on the tape arrived, 0 if none
static uint32_t lastTouchRead = 0;
static constexpr uint32_t FORGET_HOLD_MS = 5000;

void initTouch() {
  touchRead(TOUCH_PIN);  // the first reading is often off
  uint64_t sum = 0;
  for (int i = 0; i < 20; i++) {
    sum += touchRead(TOUCH_PIN);
    delay(20);
  }
  touchBaseline = sum / 20;
  Serial.printf("TOUCH_BASELINE %u\n", touchBaseline);
}

// True once per touch (when the finger arrives), not again until it is lifted.
bool wasTouched() {
  if (millis() - lastTouchRead < TOUCH_READ_MS) { return false; }
  lastTouchRead = millis();
  uint32_t v = touchRead(TOUCH_PIN);
  uint32_t threshold = touchBaseline + touchBaseline * TOUCH_RISE_PERCENT / 100;
  if (v > threshold) {
    touchCount++;
    if (touchCount >= TOUCH_NEEDED && !touchHeld) {
      touchHeld = true;
      touchStartedAt = millis();
      Serial.printf("TOUCH %u (baseline %u)\n", v, touchBaseline);
      return true;
    }
  } else {
    touchCount = 0;
    touchHeld = false;
    touchStartedAt = 0;
    touchBaseline = (touchBaseline * 31 + v) / 32;  // follow slow drift
  }
  return false;
}

// True while the finger of the last touch has stayed on the tape for FORGET_HOLD_MS (wasTouched keeps it current).
bool heldLong() {
  return touchHeld && touchStartedAt > 0 && millis() - touchStartedAt >= FORGET_HOLD_MS;
}

// Goodbye words (like the Pi's STOP_PHRASES): Puppie still answers, then the conversation ends.
// Checked in lower case. "おやすみ" alone, not "お休み", which also means a day off.
static const char* const GOODBYE_WORDS[] = {"bye", "see you", "good night", "tschüss", "tschüs", "auf wiedersehen",
                                            "bis bald", "gute nacht", "バイバイ", "ばいばい", "さようなら",
                                            "さよなら", "おやすみ", "またね"};
static bool saidGoodbye = false;

bool isGoodbye(String text) {
  text.toLowerCase();
  for (const char* word : GOODBYE_WORDS) {
    if (text.indexOf(word) >= 0) { return true; }
  }
  return false;
}

// Like the Pi version: after a reply Puppie keeps listening, and ends the conversation when nobody
// has spoken for IDLE_TIMEOUT_MS, on a goodbye word, or on a new touch / button press. The Pi waits 180 seconds
// (PUPPIE_IDLE_TIMEOUT_SEC); 30 seconds here, since a long wait made it answer family talk (2026-11-16).
static constexpr uint32_t IDLE_TIMEOUT_MS = 30000;
RoundResult talkRound(bool followUp) {
  setLed(LedState::Listening);
  int16_t* samples = (int16_t*)(body + START_BYTES + WAV_HEADER_BYTES);
  if (!record(samples) || endRequested) {
    Serial.printf("QUIET follow_up=%d\n", followUp ? 1 : 0);
    return RoundResult::Quiet;
  }
  setLed(LedState::Thinking);
  uint32_t start = millis();
  firstSoundAt = 0;
  String text = transcribe();
  if (isGoodbye(text)) {
    saidGoodbye = true;
    Serial.println("GOODBYE_WORD");
  }
  String reply = text.length() > 0 ? askLlm(text, asksAboutWeather(text)) : "";
  size_t bytes = reply.length() > 0 ? speak(withoutEmoji(reply)) : 0;
  uint32_t beforeSound = firstSoundAt > 0 ? firstSoundAt - start : 0;
  if (M5.Speaker.isRunning()) { finishSpeaking(); }
  Serial.printf("ROUND_DONE until_sound=%lu until_end=%lu\n", beforeSound, millis() - start);
  if (bytes > 0) { return RoundResult::Spoke; }
  if (followUp && text.length() == 0) { return RoundResult::Quiet; }
  setLed(LedState::Error);
  M5.delay(ERROR_SHOW_MS);
  return RoundResult::Failed;
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);
  M5.Speaker.end();
  xTaskCreate(ledTask, "led", 4096, nullptr, 1, nullptr);

  WiFi.begin(WIFI_NAME, WIFI_PASSWORD);
  WiFi.setSleep(false);
  api.useBuiltinCACertBundle();

  body = (uint8_t*)heap_caps_malloc(BODY_BYTES, MALLOC_CAP_SPIRAM);
  if (body) {
    memcpy(body, BODY_START, START_BYTES);
  }
  replyAudio = (uint8_t*)heap_caps_malloc(REPLY_AUDIO_BYTES, MALLOC_CAP_SPIRAM);
  warmUpMic();
  initTouch();
  phraseFilesOk = LittleFS.begin(true);  // true: format it the first time
  Serial.printf("PHRASE_FILES ok=%d used=%u total=%u\n", phraseFilesOk ? 1 : 0,
                phraseFilesOk ? (unsigned)LittleFS.usedBytes() : 0, phraseFilesOk ? (unsigned)LittleFS.totalBytes() : 0);
  String memory = loadMemory();
  Serial.printf("MEMORY_LOADED bytes=%u %s\n", (unsigned)memory.length(), memory.c_str());
}

void loop() {
  M5.update();
  bool online = WiFi.status() == WL_CONNECTED;
  if (!body || !replyAudio) {
    setLed(LedState::Error);
  } else if (!online) {
    setLed(LedState::Starting);
  } else if (ledState == LedState::Starting) {
    setLed(LedState::Idle);
  }
  if (millis() - lastStatus > 10000) {
    lastStatus = millis();
    Serial.printf("STATUS wifi=%s signal=%d buffer=%s\n", online ? "on" : "off", WiFi.RSSI(), body && replyAudio ? "ok" : "FAILED");
  }
  bool touched = wasTouched();
  bool pressed = M5.BtnA.wasPressed();
  if (body && replyAudio && online && (touched || pressed)) {
    // One touch starts a conversation: Puppie keeps listening after each reply on its own.
    endRequested = false;
    forgetRequested = false;
    saidGoodbye = false;
    clearHistory();
    conversationMemory = loadMemory();
    Serial.printf("MEMORY_IN_USE bytes=%u\n", (unsigned)conversationMemory.length());
    uint32_t lastSpoke = millis();
    RoundResult result = talkRound(false);
    while (!endRequested && !saidGoodbye && result != RoundResult::Failed) {
      if (result == RoundResult::Spoke) { lastSpoke = millis(); }
      if (millis() - lastSpoke > IDLE_TIMEOUT_MS) {
        Serial.println("IDLE_TIMEOUT");
        break;
      }
      result = talkRound(true);
    }
    Serial.printf("CONVERSATION_DONE ended_by_touch=%d goodbye=%d\n", endRequested ? 1 : 0, saidGoodbye ? 1 : 0);
    setLed(LedState::Thinking);
    if (!forgetRequested || !askToForget()) { updateMemory(); }
    setLed(LedState::Idle);
    touchCount = 0;
    touchHeld = true;  // a finger still on the tape must be lifted before the next touch
    touchStartedAt = 0;
  }
  M5.delay(10);
}
