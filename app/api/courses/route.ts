import { env } from "cloudflare:workers";
import { ensureLocalSchema, userFromRequest } from "../../localStore";
import { RAZ_LEVELS, type RazLesson } from "../../razData";

type CourseRow = { id: string; level: string; title: string; titleZh: string; sentencesJson: string; sourceName: string };
type D1Rows<T> = { results?: T[] };

export async function GET() {
  await ensureLocalSchema();
  const result = await env.DB.prepare(`
    SELECT id, level, title, title_zh AS titleZh, sentences_json AS sentencesJson, source_name AS sourceName
    FROM course_library ORDER BY level, id
  `).all() as D1Rows<CourseRow>;
  const lessons = (result.results || []).flatMap((row) => {
    try { return [{ id: row.id, level: row.level, title: row.title, titleZh: row.titleZh, sentences: JSON.parse(row.sentencesJson), sourceName: row.sourceName }]; }
    catch { return []; }
  });
  return Response.json({ lessons });
}

export async function PUT(request: Request) {
  const user = await userFromRequest(request);
  if (!user) return Response.json({ error: "unauthorized" }, { status: 401 });
  const body = await request.json() as { lessons?: RazLesson[]; sourceName?: string; replace?: boolean };
  const lessons = Array.isArray(body.lessons) ? body.lessons : [];
  if (!lessons.length || lessons.length > 3000) return Response.json({ error: "课程包为空或过大。" }, { status: 400 });
  for (const lesson of lessons) {
    if (!lesson.id || !lesson.title || !RAZ_LEVELS.includes(lesson.level as (typeof RAZ_LEVELS)[number]) || !Array.isArray(lesson.sentences) || !lesson.sentences.length) {
      return Response.json({ error: `课程 ${lesson.id || "未命名"} 的格式不正确。` }, { status: 400 });
    }
    if (lesson.sentences.some((sentence) => !sentence.id || !sentence.english)) {
      return Response.json({ error: `课程 ${lesson.title} 存在缺少英文原文的句子。` }, { status: 400 });
    }
  }
  await ensureLocalSchema();
  const statements = lessons.map((lesson) => env.DB.prepare(`
    INSERT INTO course_library (id, level, title, title_zh, sentences_json, source_name, imported_at)
    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO UPDATE SET level = excluded.level, title = excluded.title,
      title_zh = excluded.title_zh, sentences_json = excluded.sentences_json,
      source_name = excluded.source_name, imported_at = CURRENT_TIMESTAMP
  `).bind(lesson.id, lesson.level, lesson.title, lesson.titleZh || "", JSON.stringify(lesson.sentences), body.sourceName || "本地课程包"));
  if (body.replace) await env.DB.prepare("DELETE FROM course_library").run();
  for (let index = 0; index < statements.length; index += 100) {
    await env.DB.batch(statements.slice(index, index + 100));
  }
  return Response.json({ ok: true, imported: lessons.length });
}
