import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("defines the fully local WordQuest shell", async () => {
  const [layout, page, source, css] = await Promise.all([
    readFile(new URL("app/layout.tsx", root), "utf8"),
    readFile(new URL("app/page.tsx", root), "utf8"),
    readFile(new URL("app/WordGame.tsx", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);
  assert.match(layout, /<html lang="zh-CN">/i);
  assert.match(page, /WordQuest Local｜RAZ 原文听写/);
  assert.match(source, /正在连接本地学习库/);
  assert.doesNotMatch(css, /fonts\.googleapis|https?:\/\//i);
  assert.doesNotMatch(`${layout}${page}${source}`, /codex-preview|react-loading-skeleton/i);
});

test("implements exact-text import, local accounts, and per-user persistence", async () => {
  const [source, data, store, route, courses, start, launcher] = await Promise.all([
    readFile(new URL("app/WordGame.tsx", root), "utf8"),
    readFile(new URL("app/razData.ts", root), "utf8"),
    readFile(new URL("app/localStore.ts", root), "utf8"),
    readFile(new URL("app/api/learning-state/route.ts", root), "utf8"),
    readFile(new URL("app/api/courses/route.ts", root), "utf8"),
    readFile(new URL("package.json", root), "utf8"),
    readFile(new URL("scripts/start_wordquest.sh", root), "utf8"),
  ]);
  assert.match(data, /parseCoursePackage/);
  assert.doesNotMatch(data, /My Little World|原创训练内容/);
  assert.match(courses, /JSON\.stringify\(lesson\.sentences\)/);
  assert.match(store, /PBKDF2/);
  assert.match(route, /student_learning_state/);
  assert.match(route, /user\.id/);
  assert.match(start, /start_wordquest\.sh --hostname 0\.0\.0\.0/);
  assert.match(launcher, /vinext" dev/);
  assert.match(source, /replace\(\/\[\^A-Za-z\]\/g/);
  assert.match(source, /keepalive: true/);
  assert.doesNotMatch(source, /localStorage|sessionStorage/);
});

test("implements punctuation-aware hints, word flow, and the expanded expedition game", async () => {
  const [source, data] = await Promise.all([
    readFile(new URL("app/WordGame.tsx", root), "utf8"),
    readFile(new URL("app/razData.ts", root), "utf8"),
  ]);
  assert.match(source, /Array\.from\(word/);
  assert.match(data, /sentenceDictationWords/);
  assert.match(source, /word-punctuation/);
  assert.match(source, /lettersOnly\(wordEntry\)\.toLowerCase\(\)/);
  assert.match(source, /nextStreaks\[index\] >= 3/);
  assert.match(source, /focusNextWord\(index \+ 1\)/);
  assert.match(source, /wordInputRefs/);
  assert.match(source, /RUN_LENGTH = 8/);
  assert.match(source, /critChance/);
  assert.match(source, /结构扫描/);
  assert.match(source, /战术护盾/);
  assert.match(source, /chooseUpgrade/);
  assert.match(source, /BOSS/);
});

test("prefers reviewed RAZ clips and keeps one centralized fallback voice", async () => {
  const [source, server, launcher, aligner, batcher, qwenClient, merger] = await Promise.all([
    readFile(new URL("app/WordGame.tsx", root), "utf8"),
    readFile(new URL("scripts/tts_server.py", root), "utf8"),
    readFile(new URL("scripts/start_wordquest.sh", root), "utf8"),
    readFile(new URL("scripts/segment_raz_audio.py", root), "utf8"),
    readFile(new URL("scripts/batch_segment_raz_audio.py", root), "utf8"),
    readFile(new URL("scripts/qwen_asr_client.py", root), "utf8"),
    readFile(new URL("scripts/merge_raz_audio_batch_shards.py", root), "utf8"),
  ]);
  assert.match(source, /originalAudio\[sentence\.id\]/);
  assert.match(source, /RAZ 原版录音/);
  assert.match(aligner, /exact transcript \+ faster-whisper word timestamps \+ silence snapping/);
  assert.match(aligner, /aformat=sample_fmts=s16:channel_layouts=mono/);
  assert.match(batcher, /failure-report\.json/);
  assert.match(batcher, /--skip-failed/);
  assert.match(batcher, /--lesson-ids-file/);
  assert.match(batcher, /--validation-engine/);
  assert.match(batcher, /--alignment-engine/);
  assert.match(batcher, /stable_align_words/);
  assert.match(batcher, /Qwen3-ASR preflight passed/);
  assert.match(qwenClient, /"type": "audio_url"/);
  assert.match(batcher, /maximum-validation-failure-ratio", type=float, default=0\.0/);
  assert.match(batcher, /audio_index\.pop\(sentence\["id"\], None\)/);
  assert.match(merger, /removedStaleEntries/);
  assert.match(merger, /failed_lessons/);
  assert.match(source, /serverTtsUrl\("\/speak"\)/);
  assert.match(source, /服务器统一发音/);
  assert.match(server, /Kokoro-82M/);
  assert.match(server, /af_bella/);
  assert.match(server, /ThreadingHTTPServer/);
  assert.match(launcher, /tts_server\.py/);
});

test("keeps licensed learning material and generated media out of source control", async () => {
  const [ignore, builder, repair] = await Promise.all([
    readFile(new URL(".gitignore", root), "utf8"),
    readFile(new URL("scripts/build_raz_course_library.py", root), "utf8"),
    readFile(new URL("scripts/repair_raz_verified_text.py", root), "utf8"),
  ]);
  for (const path of ["/RAZ Book/", "/RAZ Audio/", "/data/", "/public/raz-audio/", "/tmp/", "/models/"]) {
    assert.match(ignore, new RegExp(path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(builder, /--order-map/);
  assert.match(repair, /--corrections/);
  assert.doesNotMatch(repair, /CORRECTIONS\s*=/);
});
