export type DictationSentence = {
  id: string;
  english: string;
  chinese: string;
};

export type RazLesson = {
  id: string;
  level: string;
  title: string;
  titleZh: string;
  sentences: DictationSentence[];
  sourceName?: string;
};

export const RAZ_LEVELS = [
  "aa", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N",
  "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
] as const;

export type DictationWord = { word: string; before: string; after: string };

const WORD_PATTERN = /[A-Za-z]+(?:[.'’\-‑][A-Za-z]+)*/g;

export function sentenceDictationWords(sentence: string): DictationWord[] {
  const matches = Array.from(sentence.matchAll(WORD_PATTERN));
  return matches.map((match, index) => {
    const start = match.index || 0;
    const end = start + match[0].length;
    const nextStart = matches[index + 1]?.index ?? sentence.length;
    return {
      word: match[0],
      before: index === 0 ? sentence.slice(0, start).replace(/\s/g, "") : "",
      after: sentence.slice(end, nextStart).replace(/\s/g, ""),
    };
  });
}

export function sentenceWords(sentence: string) {
  return sentenceDictationWords(sentence).map((item) => item.word);
}

function parseCsvRows(text: string) {
  const rows: string[][] = [];
  let row: string[] = [], cell = "", quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '"') {
      if (quoted && text[index + 1] === '"') { cell += '"'; index += 1; }
      else quoted = !quoted;
    } else if (char === "," && !quoted) { row.push(cell); cell = ""; }
    else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && text[index + 1] === "\n") index += 1;
      row.push(cell); if (row.some((value) => value.length)) rows.push(row); row = []; cell = "";
    } else cell += char;
  }
  row.push(cell); if (row.some((value) => value.length)) rows.push(row);
  return rows;
}

export function parseCoursePackage(text: string): RazLesson[] {
  const clean = text.replace(/^\uFEFF/, "");
  if (!clean.trim()) return [];
  if (clean.trimStart().startsWith("[") || clean.trimStart().startsWith("{")) {
    const value = JSON.parse(clean) as RazLesson[] | { lessons?: RazLesson[] };
    return Array.isArray(value) ? value : value.lessons || [];
  }
  const rows = parseCsvRows(clean);
  if (rows.length < 2) return [];
  const headers = rows[0].map((value) => value.trim().toLowerCase());
  const column = (names: string[]) => headers.findIndex((header) => names.includes(header));
  const indexes = {
    level: column(["level", "级别"]), lessonId: column(["lesson_id", "lessonid", "课程id"]),
    title: column(["lesson_title", "title", "课程名", "英文课程名"]), titleZh: column(["lesson_title_zh", "title_zh", "中文课程名"]),
    sentenceId: column(["sentence_id", "sentenceid", "句子id"]), english: column(["english", "text", "英文", "原文"]), chinese: column(["chinese", "translation", "中文", "翻译"]),
  };
  if (indexes.level < 0 || indexes.lessonId < 0 || indexes.title < 0 || indexes.english < 0) throw new Error("CSV 缺少 level、lesson_id、lesson_title 或 english 列。");
  const lessons = new Map<string, RazLesson>();
  rows.slice(1).forEach((values, rowIndex) => {
    const level = values[indexes.level]?.trim() || "";
    const lessonId = values[indexes.lessonId]?.trim() || "";
    const title = values[indexes.title] || "";
    const english = values[indexes.english] || "";
    if (!level || !lessonId || !title || !english) return;
    const key = `${level}:${lessonId}`;
    if (!lessons.has(key)) lessons.set(key, { id: lessonId, level, title, titleZh: indexes.titleZh >= 0 ? values[indexes.titleZh] || "" : "", sentences: [] });
    lessons.get(key)!.sentences.push({
      id: indexes.sentenceId >= 0 && values[indexes.sentenceId] ? values[indexes.sentenceId] : `${lessonId}-${rowIndex + 1}`,
      english,
      chinese: indexes.chinese >= 0 ? values[indexes.chinese] || "" : "",
    });
  });
  return Array.from(lessons.values());
}
