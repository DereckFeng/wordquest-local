"use client";

import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { parseCoursePackage, RAZ_LEVELS, sentenceDictationWords, sentenceWords, type RazLesson } from "./razData";

type View = "home" | "course" | "wordbook" | "game" | "library";
type WordStatus = "idle" | "correct" | "wrong" | "revealed";
type LocalUser = { id: string; username: string; displayName: string };
type GamePhase = "battle" | "upgrade" | "victory" | "defeat";

type VocabWord = { word: string; source: string; addedAt: number; correct: number; attempts: number };
type LessonProgress = { completed: number; total: number; updatedAt: number };
type GameRecord = { bestFloor: number; victories: number; highScore: number };
type LearningState = {
  vocabulary: VocabWord[];
  progress: Record<string, LessonProgress>;
  totalSentences: number;
  studyDays: string[];
  selectedLevel: string;
  game: GameRecord;
};

type RunState = {
  floor: number; hp: number; maxHp: number; armor: number; focus: number; combo: number; score: number;
  damageBonus: number; comboBonus: number; critChance: number; shieldBonus: number; enemyHp: number; enemyMaxHp: number;
};

const EMPTY_STATE: LearningState = {
  vocabulary: [], progress: {}, totalSentences: 0, studyDays: [], selectedLevel: "A",
  game: { bestFloor: 0, victories: 0, highScore: 0 },
};
const RUN_LENGTH = 8;

function today() {
  const value = new Date();
  return `${value.getFullYear()}-${value.getMonth() + 1}-${value.getDate()}`;
}

function normalizeState(value: Partial<LearningState> | null | undefined): LearningState {
  return {
    vocabulary: Array.isArray(value?.vocabulary) ? value.vocabulary : [],
    progress: value?.progress && typeof value.progress === "object" ? value.progress : {},
    totalSentences: Number(value?.totalSentences) || 0,
    studyDays: Array.isArray(value?.studyDays) ? value.studyDays : [],
    selectedLevel: RAZ_LEVELS.includes(value?.selectedLevel as (typeof RAZ_LEVELS)[number]) ? value!.selectedLevel! : "A",
    game: { ...EMPTY_STATE.game, ...(value?.game || {}) },
  };
}

function streakFrom(days: string[]) {
  const set = new Set(days); let streak = 0; const date = new Date();
  while (set.has(`${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`)) { streak += 1; date.setDate(date.getDate() - 1); }
  return streak;
}

function lettersOnly(value: string) { return value.replace(/[^A-Za-z]/g, ""); }
function dashHint(word: string) { return Array.from(word, (character) => /[A-Za-z]/.test(character) ? "-" : character).join(""); }
function serverTtsUrl(path: string) {
  const url = new URL(window.location.href);
  url.port = "3001"; url.pathname = path; url.search = ""; url.hash = "";
  return url.toString();
}
function enemyMax(floor: number) { return (52 + floor * 20) * (floor % 4 === 0 ? 2 : 1); }
function freshRun(): RunState {
  return { floor: 1, hp: 100, maxHp: 100, armor: 0, focus: 3, combo: 0, score: 0, damageBonus: 0, comboBonus: 0, critChance: .08, shieldBonus: 0, enemyHp: enemyMax(1), enemyMaxHp: enemyMax(1) };
}

function voiceScore(voice: SpeechSynthesisVoice) {
  const name = voice.name.toLowerCase(); let score = voice.lang.toLowerCase() === "en-us" ? 20 : 0;
  if (/premium|enhanced|natural|neural/.test(name)) score += 100;
  if (/ava|samantha|allison|aria|jenny|guy|daniel/.test(name)) score += 60;
  if (voice.localService) score += 10;
  return score;
}

function enemyProfile(floor: number) {
  if (floor === 8) return { name: "终章守门者", rank: "FINAL BOSS", action: "记忆抹除" };
  if (floor % 4 === 0) return { name: "失序执行官", rank: "BOSS", action: "重音干扰" };
  return [
    { name: "噪声巡猎者", rank: "STANDARD", action: "杂音突袭" },
    { name: "倒序构造体", rank: "STANDARD", action: "字序扰动" },
    { name: "静默监察者", rank: "ELITE", action: "听觉封锁" },
  ][(floor - 1) % 3];
}

export default function WordGame() {
  const [user, setUser] = useState<LocalUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [ready, setReady] = useState(false);
  const [syncState, setSyncState] = useState<"saved" | "saving" | "error">("saved");
  const [view, setView] = useState<View>("home");
  const [state, setState] = useState<LearningState>(EMPTY_STATE);
  const [courses, setCourses] = useState<RazLesson[]>([]);
  const [level, setLevel] = useState("A");
  const [lessonId, setLessonId] = useState("");
  const [sentenceIndex, setSentenceIndex] = useState(0);
  const [entries, setEntries] = useState<string[]>([]);
  const [statuses, setStatuses] = useState<WordStatus[]>([]);
  const [wrongStreaks, setWrongStreaks] = useState<number[]>([]);
  const [translationVisible, setTranslationVisible] = useState(false);
  const [lessonFinished, setLessonFinished] = useState(false);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [ttsMode, setTtsMode] = useState<"checking" | "original" | "server" | "browser">("checking");
  const [ttsLabel, setTtsLabel] = useState("正在连接服务器发音…");
  const [originalAudio, setOriginalAudio] = useState<Record<string, string>>({});
  const [wordIndex, setWordIndex] = useState(0);
  const [wordEntry, setWordEntry] = useState("");
  const [wordFeedback, setWordFeedback] = useState<"idle" | "correct" | "wrong">("idle");
  const [imported, setImported] = useState<RazLesson[]>([]);
  const [importName, setImportName] = useState("");
  const [importError, setImportError] = useState("");
  const [run, setRun] = useState<RunState>(freshRun);
  const [gamePhase, setGamePhase] = useState<GamePhase>("battle");
  const [gameEntry, setGameEntry] = useState("");
  const [gameFeedback, setGameFeedback] = useState("等待你的第一次攻击");
  const [battlePulse, setBattlePulse] = useState<"hit" | "hurt" | "critical" | "">("");
  const [scanHint, setScanHint] = useState("");
  const [hintPenalty, setHintPenalty] = useState(false);
  const loadedUser = useRef("");
  const stateRef = useRef(EMPTY_STATE);
  const wordInputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef("");
  const speechRequestRef = useRef(0);

  const lessons = useMemo(() => courses.filter((item) => item.level === level), [courses, level]);
  const lesson = courses.find((item) => item.id === lessonId) ?? lessons[0];
  const sentence = lesson?.sentences[sentenceIndex];
  const dictationWords = useMemo(() => sentence ? sentenceDictationWords(sentence.english) : [], [sentence]);
  const words = useMemo(() => dictationWords.map((item) => item.word), [dictationWords]);
  const vocab = state.vocabulary;
  const practiceWord = vocab.length ? vocab[wordIndex % vocab.length] : null;
  const gameWord = vocab.length ? vocab[wordIndex % vocab.length] : null;
  const enemy = enemyProfile(run.floor);
  const streak = streakFrom(state.studyDays);

  useEffect(() => {
    fetch("/api/auth/me", { cache: "no-store" }).then((response) => response.json()).then((body: { user?: LocalUser | null }) => {
      setUser(body.user || null); setAuthChecked(true);
    }).catch(() => setAuthChecked(true));
  }, []);

  useEffect(() => {
    if (!user || loadedUser.current === user.id) return;
    loadedUser.current = user.id;
    setReady(false);
    Promise.all([
      fetch("/api/learning-state", { cache: "no-store" }).then((response) => response.json()),
      fetch("/api/courses", { cache: "no-store" }).then((response) => response.json()),
    ]).then(([stateBody, courseBody]: [{ state?: Partial<LearningState> | null }, { lessons?: RazLesson[] }]) => {
      const nextState = normalizeState(stateBody.state);
      const nextCourses = Array.isArray(courseBody.lessons) ? courseBody.lessons : [];
      const availableLevel = nextCourses.some((item) => item.level === nextState.selectedLevel)
        ? nextState.selectedLevel : nextCourses[0]?.level || nextState.selectedLevel;
      setState(nextState); setCourses(nextCourses); setLevel(availableLevel);
      setLessonId(nextCourses.find((item) => item.level === availableLevel)?.id || "");
      queueMicrotask(() => setReady(true));
    }).catch(() => setReady(true));
  }, [user]);

  useEffect(() => {
    if (!ready || !user) return;
    const timer = window.setTimeout(async () => {
      setSyncState("saving");
      try {
        const response = await fetch("/api/learning-state", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ state }) });
        if (!response.ok) throw new Error("save failed");
        setSyncState("saved");
      } catch { setSyncState("error"); }
    }, 550);
    return () => window.clearTimeout(timer);
  }, [state, ready, user]);

  useEffect(() => { stateRef.current = state; }, [state]);

  useEffect(() => {
    if (!ready || !user) return;
    const saveBeforeLeaving = () => {
      void fetch("/api/learning-state", {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({ state: stateRef.current }), keepalive: true,
      });
    };
    const saveWhenHidden = () => { if (document.visibilityState === "hidden") saveBeforeLeaving(); };
    window.addEventListener("pagehide", saveBeforeLeaving);
    document.addEventListener("visibilitychange", saveWhenHidden);
    return () => {
      window.removeEventListener("pagehide", saveBeforeLeaving);
      document.removeEventListener("visibilitychange", saveWhenHidden);
    };
  }, [ready, user]);

  useEffect(() => {
    if (!("speechSynthesis" in window)) return;
    const loadVoices = () => {
      const english = window.speechSynthesis.getVoices().filter((voice) => voice.lang.toLowerCase().startsWith("en")).sort((a, b) => voiceScore(b) - voiceScore(a));
      setVoices(english);
    };
    loadVoices(); window.speechSynthesis.addEventListener("voiceschanged", loadVoices);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", loadVoices);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch(serverTtsUrl("/health"), { signal: controller.signal })
      .then((response) => { if (!response.ok) throw new Error("tts unavailable"); return response.json(); })
      .then((body: { engine?: string; voice?: string }) => {
        setTtsMode("server"); setTtsLabel(`${body.engine || "服务器语音"} · ${body.voice || "统一音色"}`);
      })
      .catch(() => { if (!controller.signal.aborted) { setTtsMode("browser"); setTtsLabel("服务器语音未启动，暂用本机声音"); } });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/raz-audio/index.json", { signal: controller.signal })
      .then((response) => response.ok ? response.json() : {})
      .then((body: Record<string, string>) => setOriginalAudio(body))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => () => {
    audioRef.current?.pause();
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
  }, []);

  async function speak(text: string, slower = false, originalUrl = "") {
    const requestId = ++speechRequestRef.current;
    audioRef.current?.pause();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    if (originalUrl) {
      try {
        if (audioUrlRef.current) { URL.revokeObjectURL(audioUrlRef.current); audioUrlRef.current = ""; }
        const audio = new Audio(originalUrl);
        audio.playbackRate = slower ? .78 : 1;
        audio.preservesPitch = true;
        audioRef.current = audio;
        await audio.play();
        setTtsMode("original");
        setTtsLabel(slower ? "原版课程录音 · 0.78× 慢速" : "原版课程录音 · 1.0× 原速");
        return;
      } catch {
        if (requestId !== speechRequestRef.current) return;
      }
    }
    try {
      const response = await fetch(serverTtsUrl("/speak"), {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, slow: slower }),
      });
      if (!response.ok) throw new Error("server tts unavailable");
      const blob = await response.blob();
      if (requestId !== speechRequestRef.current) return;
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      const url = URL.createObjectURL(blob); audioUrlRef.current = url;
      const audio = new Audio(url); audioRef.current = audio;
      audio.addEventListener("ended", () => { if (audioUrlRef.current === url) { URL.revokeObjectURL(url); audioUrlRef.current = ""; } }, { once: true });
      await audio.play();
      const engine = response.headers.get("X-TTS-Engine") || "服务器语音";
      const voice = response.headers.get("X-TTS-Voice") || "统一音色";
      setTtsMode("server"); setTtsLabel(`${engine} · ${voice}`);
    } catch {
      if (requestId !== speechRequestRef.current || !("speechSynthesis" in window)) return;
      setTtsMode("browser"); setTtsLabel("服务器语音未启动，暂用本机声音");
      const utterance = new SpeechSynthesisUtterance(text);
      const voice = voices[0];
      utterance.voice = voice ?? null; utterance.lang = voice?.lang || "en-US"; utterance.rate = slower ? .68 : .82; utterance.pitch = 1;
      window.speechSynthesis.speak(utterance);
    }
  }

  function updateState(update: (current: LearningState) => LearningState) { setState((current) => update(current)); }

  function addVocabulary(word: string, source: string) {
    updateState((current) => current.vocabulary.some((item) => item.word === word) ? current : {
      ...current, vocabulary: [...current.vocabulary, { word, source, addedAt: Date.now(), correct: 0, attempts: 0 }],
    });
  }

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    loadedUser.current = ""; setUser(null); setReady(false); setState(EMPTY_STATE); setCourses([]); setView("home");
  }

  function chooseLevel(nextLevel: string) {
    setLevel(nextLevel); setLessonId(courses.find((item) => item.level === nextLevel)?.id || "");
    updateState((current) => ({ ...current, selectedLevel: nextLevel }));
  }

  function openLesson(nextLessonId: string) {
    const selected = courses.find((item) => item.id === nextLessonId);
    if (!selected) return;
    setLessonId(nextLessonId); setSentenceIndex(0); setLessonFinished(false); setView("course"); resetSentence(selected.sentences[0]?.english || "");
  }

  function resetSentence(english: string) {
    const length = sentenceWords(english).length;
    setEntries(Array(length).fill("")); setStatuses(Array(length).fill("idle")); setWrongStreaks(Array(length).fill(0)); setTranslationVisible(false);
    wordInputRefs.current = [];
    window.setTimeout(() => focusNextWord(0), 0);
  }

  function updateEntry(index: number, value: string) {
    setEntries((current) => current.map((item, itemIndex) => itemIndex === index ? value : item));
    setStatuses((current) => current.map((item, itemIndex) => itemIndex === index && item === "wrong" ? "idle" : item));
    const targetLength = lettersOnly(words[index] || "").length;
    if (targetLength > 0 && lettersOnly(value).length >= targetLength) focusNextWord(index + 1);
  }

  function focusNextWord(startIndex: number) {
    window.requestAnimationFrame(() => {
      for (let index = startIndex; index < wordInputRefs.current.length; index += 1) {
        const input = wordInputRefs.current[index];
        if (input && !input.disabled) { input.focus(); input.select(); return; }
      }
    });
  }

  function returnToPreviousWord(index: number) {
    for (let previous = index - 1; previous >= 0; previous -= 1) {
      const input = wordInputRefs.current[previous];
      if (input && !input.disabled) { input.focus(); input.select(); return; }
    }
  }

  function submitSentence(event: FormEvent) {
    event.preventDefault(); if (!sentence || !lesson) return;
    const nextStatuses = [...statuses], nextStreaks = [...wrongStreaks], nextEntries = [...entries];
    words.forEach((word, index) => {
      if (statuses[index] === "correct" || statuses[index] === "revealed") return;
      if (lettersOnly(entries[index] || "") === lettersOnly(word)) { nextStatuses[index] = "correct"; nextStreaks[index] = 0; }
      else {
        nextStreaks[index] = (nextStreaks[index] || 0) + 1;
        if (nextStreaks[index] >= 3) { nextStatuses[index] = "revealed"; nextEntries[index] = word; addVocabulary(word, `${lesson.level} · ${lesson.title}`); }
        else { nextStatuses[index] = "wrong"; nextEntries[index] = ""; }
      }
    });
    setStatuses(nextStatuses); setWrongStreaks(nextStreaks); setEntries(nextEntries);
    if (nextStatuses.every((item) => item === "correct" || item === "revealed")) {
      setTranslationVisible(true);
      updateState((current) => ({
        ...current, totalSentences: current.totalSentences + 1, studyDays: Array.from(new Set([...current.studyDays, today()])),
        progress: { ...current.progress, [lesson.id]: { completed: Math.max(current.progress[lesson.id]?.completed || 0, sentenceIndex + 1), total: lesson.sentences.length, updatedAt: Date.now() } },
      }));
    } else {
      const firstRetry = nextStatuses.findIndex((item) => item === "wrong");
      if (firstRetry >= 0) window.setTimeout(() => focusNextWord(firstRetry), 0);
    }
  }

  function nextSentence() {
    if (!lesson || sentenceIndex + 1 >= lesson.sentences.length) { setLessonFinished(true); return; }
    const next = sentenceIndex + 1; setSentenceIndex(next); resetSentence(lesson.sentences[next].english);
  }

  function startWordbook() { setWordIndex(0); setWordEntry(""); setWordFeedback("idle"); setView("wordbook"); }

  function submitWord(event: FormEvent) {
    event.preventDefault(); if (!practiceWord) return;
    const correct = lettersOnly(wordEntry).toLowerCase() === lettersOnly(practiceWord.word).toLowerCase(); setWordFeedback(correct ? "correct" : "wrong");
    updateState((current) => ({ ...current, vocabulary: current.vocabulary.map((item) => item.word === practiceWord.word ? { ...item, attempts: item.attempts + 1, correct: item.correct + (correct ? 1 : 0) } : item) }));
  }

  function nextWord() { setWordIndex((current) => (current + 1) % Math.max(vocab.length, 1)); setWordEntry(""); setWordFeedback("idle"); }

  async function handleCourseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file) return;
    setImportName(file.name); setImportError("");
    try {
      const parsed = parseCoursePackage(await file.text());
      if (!parsed.length) throw new Error("课程包中没有找到课程。");
      setImported(parsed);
    } catch (error) { setImported([]); setImportError(error instanceof Error ? error.message : "课程包无法读取。"); }
  }

  async function saveCourseImport() {
    if (!imported.length) return;
    const response = await fetch("/api/courses", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ lessons: imported, sourceName: importName, replace: true }) });
    const body = await response.json() as { error?: string };
    if (!response.ok) { setImportError(body.error || "保存失败。"); return; }
    const fresh = await fetch("/api/courses", { cache: "no-store" }).then((result) => result.json()) as { lessons?: RazLesson[] };
    const next = fresh.lessons || []; setCourses(next); setImported([]); setLevel(next[0]?.level || "A"); setLessonId(next[0]?.id || ""); setView("home");
  }

  function startBattle() {
    setRun(freshRun()); setGamePhase("battle"); setWordIndex(Math.floor(Math.random() * Math.max(vocab.length, 1))); setGameEntry(""); setGameFeedback("听清目标词，建立第一次连击"); setBattlePulse(""); setScanHint(""); setHintPenalty(false); setView("game");
  }

  function finishRun(result: "victory" | "defeat", final: RunState) {
    setGamePhase(result);
    updateState((current) => ({ ...current, game: {
      bestFloor: Math.max(current.game.bestFloor, final.floor), highScore: Math.max(current.game.highScore, final.score), victories: current.game.victories + (result === "victory" ? 1 : 0),
    } }));
  }

  function submitBattle(event: FormEvent) {
    event.preventDefault(); if (!gameWord || gamePhase !== "battle") return;
    const correct = lettersOnly(gameEntry) === lettersOnly(gameWord.word);
    updateState((current) => ({ ...current, vocabulary: current.vocabulary.map((item) => item.word === gameWord.word ? { ...item, attempts: item.attempts + 1, correct: item.correct + (correct ? 1 : 0) } : item) }));
    if (correct) {
      const nextCombo = run.combo + 1;
      const critical = Math.random() < run.critChance + Math.min(.2, nextCombo * .015);
      const base = 15 + lettersOnly(gameWord.word).length * 2 + run.damageBonus + nextCombo * (2 + run.comboBonus);
      const damage = Math.max(1, Math.round(base * (critical ? 1.75 : 1) * (hintPenalty ? .7 : 1)));
      const nextEnemyHp = Math.max(0, run.enemyHp - damage);
      const nextRun = { ...run, combo: nextCombo, focus: Math.min(5, run.focus + (nextCombo % 3 === 0 ? 1 : 0)), score: run.score + damage * (10 + nextCombo), enemyHp: nextEnemyHp };
      setRun(nextRun); setBattlePulse(critical ? "critical" : "hit"); setGameFeedback(`${critical ? "暴击 · " : ""}${damage} 伤害 · ${nextCombo} 连击`); setGameEntry(""); setScanHint(""); setHintPenalty(false);
      if (nextEnemyHp <= 0) window.setTimeout(() => run.floor >= RUN_LENGTH ? finishRun("victory", nextRun) : setGamePhase("upgrade"), 500);
      window.setTimeout(() => setBattlePulse(""), 520);
    } else {
      const rawDamage = 9 + run.floor * 3 + (run.floor % 4 === 0 ? 8 : 0);
      const absorbed = Math.min(run.armor, rawDamage); const nextArmor = run.armor - absorbed; const nextHp = Math.max(0, run.hp - (rawDamage - absorbed));
      const nextRun = { ...run, hp: nextHp, armor: nextArmor, combo: 0 };
      setRun(nextRun); setBattlePulse("hurt"); setGameFeedback(`${enemy.action}造成 ${rawDamage - absorbed} 伤害，连击中断`); setGameEntry("");
      if (nextHp <= 0) window.setTimeout(() => finishRun("defeat", nextRun), 450);
      window.setTimeout(() => setBattlePulse(""), 520);
    }
  }

  function scanWord() {
    if (!gameWord || run.focus < 1 || scanHint) return;
    const target = lettersOnly(gameWord.word); setRun((current) => ({ ...current, focus: current.focus - 1 })); setScanHint(`${target.slice(0, 1)}${"-".repeat(Math.max(0, target.length - 1))}`); setHintPenalty(true);
  }

  function raiseShield() {
    if (run.focus < 2) return;
    setRun((current) => ({ ...current, focus: current.focus - 2, armor: current.armor + 14 + current.shieldBonus })); setGameFeedback(`战术护盾就绪 · ${14 + run.shieldBonus} 点护甲`);
  }

  function chooseUpgrade(type: "damage" | "vitality" | "combo") {
    const nextFloor = run.floor + 1; const hp = enemyMax(nextFloor);
    setRun((current) => ({
      ...current, floor: nextFloor, enemyHp: hp, enemyMaxHp: hp, focus: Math.min(5, current.focus + 1), armor: current.armor + current.shieldBonus,
      ...(type === "damage" ? { damageBonus: current.damageBonus + 7, critChance: current.critChance + .03 } : {}),
      ...(type === "vitality" ? { maxHp: current.maxHp + 16, hp: Math.min(current.maxHp + 16, current.hp + 28), shieldBonus: current.shieldBonus + 3 } : {}),
      ...(type === "combo" ? { comboBonus: current.comboBonus + 2, focus: 5 } : {}),
    }));
    setWordIndex((current) => (current + 1) % Math.max(vocab.length, 1)); setGamePhase("battle"); setGameEntry(""); setScanHint(""); setHintPenalty(false); setGameFeedback(`第 ${nextFloor} 层信号接入`);
  }

  if (!authChecked) return <div className="loading-screen"><span>W</span><p>正在连接本地学习库…</p></div>;
  if (!user) return <LocalAuth onAuthenticated={(nextUser) => { setUser(nextUser); setAuthChecked(true); }} />;
  if (!ready) return <div className="loading-screen"><span>W</span><p>正在读取 {user.displayName} 的本地进度…</p></div>;

  return <div className={`app ${view === "course" ? "focus-mode" : ""}`}>
    {view !== "course" && <header className="topbar">
      <button className="brand" onClick={() => setView("home")} aria-label="返回首页"><span className="brand-logo">W</span><span><b>WordQuest</b><small>RAZ 原文听写 · 本地版</small></span></button>
      <nav className="main-nav" aria-label="主要功能">
        <button className={view === "home" || view === "course" ? "active" : ""} onClick={() => setView("home")}><span>◉</span> 课程听写</button>
        <button className={view === "wordbook" ? "active" : ""} onClick={startWordbook}><span>▣</span> 单词本 <i>{vocab.length}</i></button>
        <button className={view === "game" ? "active" : ""} onClick={startBattle}><span>◆</span> 远征模式</button>
        <button className={view === "library" ? "active" : ""} onClick={() => setView("library")}><span>⇧</span> 课程库</button>
      </nav>
      <div className="account-area"><span className={`sync-dot ${syncState}`}></span><div className="avatar">{user.displayName.slice(0, 1).toUpperCase()}</div><div className="account-copy"><b>{user.displayName}</b><small>{syncState === "saving" ? "正在保存到服务器…" : syncState === "error" ? "服务器保存失败，请检查连接" : "进度已保存到服务器"}</small></div><button className="logout" onClick={logout} title="退出登录">退出</button></div>
    </header>}

    {view === "home" && <main className="dashboard">
      <section className="hero-panel"><div className="hero-copy"><span className="pill"><i></i> LOCAL LEARNING SERVER</span><h1>听见原文，<br/><em>准确写下来。</em></h1><p>课程文本来自你导入的正版 RAZ 课程包，系统逐字保存，不改写、不替换。<br/>学生在同一局域网登录，各自的进度和单词本都保存在服务器。</p><div className="hero-actions"><button className="primary" onClick={() => lessons[0] ? openLesson(lessons[0].id) : setView("library")}>{lessons.length ? "继续学习" : "导入 RAZ 课程"} <span>→</span></button><button className="sound-check" onClick={() => speak("Ready for today's listening challenge?")}><span>▶</span> 试听统一发音</button></div><div className="guest-note local-note"><span>⌂</span><p><b>局域网服务器保存</b><small>账号、课程、进度和单词本均存储在运行网站的服务器上</small></p><button onClick={() => setView("library")}>管理课程 →</button></div></div><div className="hero-art" aria-hidden="true"><div className="sun-dot"></div><div className="speech-bubble">Listen.</div><div className="headphones"><span></span></div><div className="book-shape"><i>A</i><b>B</b><em>C</em></div><div className="sound-wave"><i></i><i></i><i></i><i></i></div></div></section>
      <section className="quick-stats"><article><span className="stat-icon peach">✓</span><div><b>{state.totalSentences}</b><p>已完成句子</p></div><small>服务器记录</small></article><article><span className="stat-icon mint">▣</span><div><b>{vocab.length}</b><p>单词本词汇</p></div><small>{vocab.filter((item) => item.correct >= 5).length} 个已熟练</small></article><article><span className="stat-icon yellow">↗</span><div><b>{streak}</b><p>连续学习天数</p></div><small>最佳远征 {state.game.bestFloor} 层</small></article></section>
      <section className="course-section"><div className="section-title"><div><span className="eyebrow">AUTHORIZED COURSE LIBRARY</span><h2>选择 RAZ 级别与原文课程</h2></div><p>英文原文按导入内容逐字使用</p></div><div className="level-scroller" role="tablist" aria-label="RAZ 级别">{RAZ_LEVELS.map((item) => { const count = courses.filter((course) => course.level === item).length; return <button key={item} role="tab" aria-selected={level === item} className={level === item ? "active" : ""} onClick={() => chooseLevel(item)}>{item}<small>{count || "—"} 课</small></button>; })}</div>
        {!lessons.length ? <div className="no-course"><span>RAZ {level}</span><h3>这个级别还没有导入课程</h3><p>请导入你拥有合法使用权的 RAZ 文本课程包。系统不会生成或改写原文。</p><button onClick={() => setView("library")}>打开本地课程库 →</button></div> : <div className="lesson-grid">{lessons.map((item, index) => { const progress = state.progress[item.id]?.completed || 0; const percent = Math.round(progress / item.sentences.length * 100); return <article className="lesson-card" key={item.id}><div className={`lesson-number color-${index % 4 + 1}`}><small>LESSON</small><b>{String(index + 1).padStart(2, "0")}</b></div><div className="lesson-copy"><span>RAZ {item.level} · {item.sourceName || "本地原文"}</span><h3>{item.title}</h3><p>{item.titleZh || "原文课程"} · {item.sentences.length} 个句子</p><div className="progress-line"><i style={{ width: `${percent}%` }}></i></div><small>{percent ? `已完成 ${percent}%` : "尚未开始"}</small></div><button onClick={() => openLesson(item.id)}>{progress ? "继续" : "开始"} <span>→</span></button></article>; })}</div>}
      </section>
      <section className="bottom-cards"><article className="wordbook-promo"><div><span>WORD BOOK</span><h2>错三次的词，<br/>进入个人单词本。</h2><p>听音拼写，把薄弱点逐个练熟。</p><button onClick={startWordbook}>开始复习 →</button></div><div className="stacked-cards"><i>listen</i><i>spell</i><i>master</i></div></article><article className="game-promo tactical"><div className="tactical-core"><i></i><b></b></div><div><span>THE ECHO EXPEDITION</span><h2>回声远征 · 8 层战役</h2><p>连击、专注点、护盾、暴击、Boss 与局内强化选择</p><button onClick={startBattle} disabled={!vocab.length}>进入远征 ◆</button></div></article></section>
    </main>}

    {view === "library" && <main className="focused-page"><div className="focus-header"><button className="back-button" onClick={() => setView("home")}>← 返回首页</button><div><span>LOCAL COURSE LIBRARY</span><h1>本地 RAZ 课程库</h1></div><p>{courses.length} 门课程 · 保存在服务器</p></div><section className="library-card"><div className="library-intro"><span>原文保护</span><h2>导入你有权使用的 RAZ 课程文本</h2><p>导入后，英文大小写、标点、缩写和措辞都会原样保存。听写时只隐藏原文，不会对内容进行任何改写。</p></div><label className="course-upload"><input type="file" accept=".csv,.json,text/csv,application/json" onChange={handleCourseFile}/><span>⇧</span><b>{importName || "选择 CSV 或 JSON 课程包"}</b><small>点击选择要导入的本地文件</small></label>{importError && <p className="import-error">{importError}</p>}{imported.length > 0 && <div className="import-preview"><div><span>读取成功</span><b>{imported.length} 门课程</b><small>{imported.reduce((sum, item) => sum + item.sentences.length, 0)} 个原文句子 · {Array.from(new Set(imported.map((item) => item.level))).join(" / ")}</small></div><button onClick={saveCourseImport}>替换并保存到服务器 →</button></div>}<details className="format-help"><summary>查看课程包格式</summary><p>CSV 表头：<code>level,lesson_id,lesson_title,lesson_title_zh,sentence_id,english,chinese</code></p><p>每行一个句子；<code>english</code> 中的内容会逐字保留。JSON 也可以直接使用同名字段。</p></details><div className="copyright-note"><b>内容说明</b><p>本项目不内置或抓取 RAZ 原文。请只导入学校、家庭或机构已获得合法授权的课程文本。</p></div></section></main>}

    {view === "course" && lesson && <main className="practice-page"><div className="practice-topline"><button className="back-button" onClick={() => setView("home")}>← 课程列表</button><div><span>RAZ {lesson.level}</span><b>{lesson.title}</b></div><p>第 {Math.min(sentenceIndex + 1, lesson.sentences.length)} / {lesson.sentences.length} 句</p></div>{lessonFinished ? <section className="finish-panel"><span>★</span><p>LESSON COMPLETE</p><h1>这节课听完了</h1><p>原文听写已完成，连续错误三次的词已加入 {user.displayName} 的单词本。</p><div><button className="primary" onClick={() => setView("home")}>返回课程</button><button onClick={startWordbook}>复习单词本</button></div></section> : sentence && <section className="dictation-card"><div className="dictation-heading"><h1>听清，再写下来</h1><p>横线代表字母 · 标点会显示但不参与判定 · 区分大小写</p></div><div className="audio-console focus-audio"><button className="play-main focus-play" onClick={() => speak(sentence.english, false, originalAudio[sentence.id])} aria-label="播放句子"><span>▶</span><b>播放句子</b></button><div className={`tts-status ${ttsMode}`}><b>{ttsMode === "original" ? "RAZ 原版录音" : ttsMode === "server" ? "服务器统一发音" : ttsMode === "checking" ? "连接发音服务器" : "本机临时发音"}</b><small>{ttsLabel}</small></div><button className="slow-button" onClick={() => speak(sentence.english, true, originalAudio[sentence.id])}>慢速播放</button></div><form onSubmit={submitSentence} className="dictation-form"><div className="word-inputs">{dictationWords.map(({ word, before, after }, index) => <label key={`${sentence.id}-${index}`} className={statuses[index] || "idle"} style={{ "--letter-count": Math.min(14, Math.max(4, lettersOnly(word).length)) } as CSSProperties}>{before && <i className="word-punctuation before">{before}</i>}<input ref={(node) => { wordInputRefs.current[index] = node; }} autoFocus={index === 0} value={entries[index] || ""} placeholder={dashHint(word)} onChange={(event) => updateEntry(index, event.target.value)} onKeyDown={(event) => { if (event.key === "Backspace" && !entries[index]) { event.preventDefault(); returnToPreviousWord(index); } }} disabled={statuses[index] === "correct" || statuses[index] === "revealed"} autoCapitalize="off" autoComplete="off" spellCheck={false} aria-label={`第 ${index + 1} 个单词，${lettersOnly(word).length} 个字母`}/>{statuses[index] === "correct" && <span>✓</span>}{statuses[index] === "wrong" && <em>再听一次 · {wrongStreaks[index]}/3</em>}{statuses[index] === "revealed" && <em>正确词：{word} · 已加入单词本</em>}{after && <i className="word-punctuation after">{after}</i>}</label>)}</div>{!translationVisible ? <button className="submit-dictation" type="submit">提交检查 <span>→</span></button> : <div className="translation-card"><span>✓</span><div><small>听写完成 · 句子翻译</small><p>{sentence.chinese || "课程包未提供中文翻译"}</p></div><button type="button" onClick={nextSentence}>{sentenceIndex + 1 === lesson.sentences.length ? "完成课程" : "下一句"} →</button></div>}</form></section>}</main>}

    {view === "wordbook" && <main className="focused-page"><div className="focus-header"><button className="back-button" onClick={() => setView("home")}>← 返回首页</button><div><span>{user.displayName.toUpperCase()} · WORD BOOK</span><h1>个人单词本听写</h1></div><p>{vocab.length} 个待掌握单词</p></div>{!practiceWord ? <section className="empty-state"><span>▣</span><h2>单词本还是空的</h2><p>课程听写中，一个词连续拼错 3 次后，会自动收进这里。</p><button className="primary" onClick={() => setView("home")}>开始课程听写</button></section> : <section className="word-practice-card"><div className="word-progress"><span>本轮 {wordIndex + 1} / {vocab.length}</span><i><b style={{ width: `${((wordIndex + 1) / vocab.length) * 100}%` }}></b></i><small>来源：{practiceWord.source}</small></div><span className="ear-icon">◖</span><h2>听发音，拼出这个单词</h2><button className="round-listen" onClick={() => speak(practiceWord.word)}>▶</button><button className="replay" onClick={() => speak(practiceWord.word, true)}>慢速再听一遍</button><form onSubmit={submitWord}><input autoFocus value={wordEntry} onChange={(event) => { setWordEntry(event.target.value); setWordFeedback("idle"); }} placeholder={dashHint(practiceWord.word)} autoComplete="off" spellCheck={false}/><button type="submit">检查拼写</button></form>{wordFeedback === "correct" && <div className="word-result correct"><b>✓ 拼对了！</b><button onClick={nextWord}>下一个单词 →</button></div>}{wordFeedback === "wrong" && <div className="word-result wrong"><b>还差一点，再听一次。</b><span>单词本练习不区分大小写，标点也不影响结果。</span></div>}<div className="mastery-row"><span>熟练度</span><i>{[0,1,2,3,4].map((item) => <b key={item} className={item < Math.min(5, practiceWord.correct) ? "filled" : ""}></b>)}</i><small>{practiceWord.correct >= 5 ? "已掌握" : `再答对 ${Math.max(0, 5 - practiceWord.correct)} 次`}</small></div></section>}</main>}

    {view === "game" && <main className="expedition-page"><div className="expedition-top"><button onClick={() => setView("home")}>← 撤离远征</button><div><span>THE ECHO EXPEDITION</span><h1>回声远征</h1></div><p>最高 {state.game.bestFloor} 层 · {state.game.highScore.toLocaleString()} 分</p></div>{!gameWord ? <section className="empty-state dark-empty"><span>◇</span><h2>没有可装备的词汇</h2><p>先在课程中收集至少一个错词，再进入远征。</p><button className="primary" onClick={() => setView("home")}>前往课程</button></section> : <section className={`expedition-shell ${battlePulse}`}>
        <div className="route-map">{Array.from({length: RUN_LENGTH}, (_, index) => <span key={index} className={`${index + 1 < run.floor ? "cleared" : ""} ${index + 1 === run.floor ? "current" : ""} ${(index + 1) % 4 === 0 ? "boss" : ""}`}><i>{index + 1}</i>{index < RUN_LENGTH - 1 && <b></b>}</span>)}</div>
        <div className="combat-hud"><div className="operator-card"><span>{user.displayName}</span><b>{run.hp} / {run.maxHp} HP</b><i><em style={{width:`${run.hp / run.maxHp * 100}%`}}></em></i><small>护甲 {run.armor} · 专注 {run.focus}/5</small></div><div className="mission-data"><small>SECTOR {String(run.floor).padStart(2,"0")}</small><b>{run.score.toLocaleString()}</b><span>SCORE</span></div><div className="enemy-card"><span>{enemy.rank}</span><b>{enemy.name}</b><i><em style={{width:`${run.enemyHp / run.enemyMaxHp * 100}%`}}></em></i><small>{run.enemyHp} / {run.enemyMaxHp} INTEGRITY</small></div></div>
        {gamePhase === "battle" && <><div className="tactical-arena"><div className="operator-sigil"><span></span><i></i></div><div className="signal-stream">{Array.from({length:7},(_,i)=><i key={i}></i>)}</div><div className="rune-enemy"><span></span><i></i><b></b></div><div className="damage-flash">{battlePulse === "critical" ? "CRITICAL" : battlePulse === "hit" ? "HIT" : battlePulse === "hurt" ? "BREACH" : ""}</div></div><div className="combat-console"><div className="combo-panel"><span>COMBO</span><b>×{run.combo}</b><small>{run.combo >= 5 ? "伤害增幅已激活" : `再连续命中 ${Math.max(0,5-run.combo)} 次激活增幅`}</small></div><div className="spell-panel"><p>监听目标信号并准确输入；大小写会影响命中。</p><div className="audio-actions"><button onClick={() => speak(gameWord.word)}>▶ 标准播放</button><button onClick={() => speak(gameWord.word, true)}>◌ 慢速解析</button></div>{scanHint && <div className="scan-result">结构扫描：<b>{scanHint}</b><small>本次伤害降低 30%</small></div>}<form onSubmit={submitBattle}><input autoFocus value={gameEntry} onChange={(event) => setGameEntry(event.target.value)} placeholder={scanHint || dashHint(gameWord.word)} autoComplete="off" spellCheck={false}/><button type="submit">确认攻击</button></form><strong className={battlePulse}>{gameFeedback}</strong></div><div className="ability-panel"><button onClick={scanWord} disabled={run.focus < 1 || Boolean(scanHint)}><span>01</span><b>结构扫描</b><small>1 专注 · 显示首字母</small></button><button onClick={raiseShield} disabled={run.focus < 2}><span>02</span><b>战术护盾</b><small>2 专注 · 吸收伤害</small></button><div><span>暴击率</span><b>{Math.round(run.critChance*100)}%</b><small>当前伤害 +{run.damageBonus}</small></div></div></div></>}
        {gamePhase === "upgrade" && <div className="upgrade-screen"><span>SECTOR CLEARED</span><h2>选择一项局内强化</h2><p>强化仅在本次 8 层远征中生效。</p><div><button onClick={() => chooseUpgrade("damage")}><i>01</i><b>锐化听觉</b><span>基础伤害 +7<br/>暴击率 +3%</span></button><button onClick={() => chooseUpgrade("vitality")}><i>02</i><b>记忆装甲</b><span>生命上限 +16<br/>恢复 28 HP</span></button><button onClick={() => chooseUpgrade("combo")}><i>03</i><b>连锁共振</b><span>连击伤害 +2<br/>专注恢复至 5</span></button></div></div>}
        {(gamePhase === "victory" || gamePhase === "defeat") && <div className="run-summary"><span>{gamePhase === "victory" ? "EXPEDITION COMPLETE" : "SIGNAL LOST"}</span><h2>{gamePhase === "victory" ? "你穿越了全部 8 层" : `远征止步于第 ${run.floor} 层`}</h2><div><p><b>{run.score.toLocaleString()}</b><small>本局分数</small></p><p><b>×{run.combo}</b><small>最终连击</small></p><p><b>{state.game.bestFloor}</b><small>历史最高层</small></p></div><button onClick={startBattle}>重新开始远征 →</button></div>}
      </section>}</main>}
    {view !== "course" && <footer><span><b>W</b> WordQuest Local</span><p>原文课程 · 服务器账号 · 局域网学习</p><small>课程文本由使用者自行导入并负责授权。</small></footer>}
  </div>;
}

function LocalAuth({ onAuthenticated }: { onAuthenticated: (user: LocalUser) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState(""); const [displayName, setDisplayName] = useState(""); const [password, setPassword] = useState("");
  const [error, setError] = useState(""); const [submitting, setSubmitting] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(""); setSubmitting(true);
    try {
      const response = await fetch(`/api/auth/${mode}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username, displayName, password }) });
      const body = await response.json() as { user?: LocalUser; error?: string };
      if (!response.ok || !body.user) setError(body.error || "登录失败。"); else onAuthenticated(body.user);
    } catch { setError("无法连接本地学习服务器。"); } finally { setSubmitting(false); }
  }
  return <main className="auth-page"><section className="auth-brand"><span className="brand-logo">W</span><p>WORDQUEST LOCAL</p><h1>一台电脑，<br/>一间英语听写教室。</h1><p>学生使用同一局域网访问。每个人的课程进度、错词与远征记录都保存在主机，不上传云端。</p><div><i></i><span><b>LOCAL ONLY</b><small>账号与学习数据仅保存在本地数据库</small></span></div></section><section className="auth-card"><div className="auth-tabs"><button className={mode === "login" ? "active" : ""} onClick={() => {setMode("login");setError("");}}>学生登录</button><button className={mode === "register" ? "active" : ""} onClick={() => {setMode("register");setError("");}}>创建账号</button></div><span className="auth-kicker">{mode === "login" ? "WELCOME BACK" : "NEW LOCAL PROFILE"}</span><h2>{mode === "login" ? "继续你的学习进度" : "创建本地学生档案"}</h2><p>{mode === "login" ? "在这台学习服务器上登录。" : "账号只在当前局域网主机中有效。"}</p><form onSubmit={submit}>{mode === "register" && <label><span>学生姓名</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="例如：小明" autoComplete="name"/></label>}<label><span>用户名</span><input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="2–24 个字符" autoComplete="username"/></label><label><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="至少 6 位" autoComplete={mode === "login" ? "current-password" : "new-password"}/></label>{error && <div className="auth-error">{error}</div>}<button type="submit" disabled={submitting}>{submitting ? "正在连接…" : mode === "login" ? "登录并继续 →" : "创建本地账号 →"}</button></form><small>请让教师或家长保持主机上的 WordQuest 窗口运行。</small></section></main>;
}
