import { env } from "cloudflare:workers";
import { createSession, ensureLocalSchema, hashPassword, normalizeUsername, one, safeEqual } from "../../../localStore";

type StudentRow = { id: string; username: string; displayName: string; passwordHash: string; passwordSalt: string };

export async function POST(request: Request) {
  const body = await request.json() as { username?: string; password?: string };
  const username = normalizeUsername(body.username || "");
  await ensureLocalSchema();
  const student = await one<StudentRow>(env.DB.prepare(`
    SELECT id, username, display_name AS displayName, password_hash AS passwordHash, password_salt AS passwordSalt
    FROM students WHERE username = ?
  `).bind(username));
  if (!student || !safeEqual(await hashPassword(body.password || "", student.passwordSalt), student.passwordHash)) {
    return Response.json({ error: "用户名或密码不正确。" }, { status: 401 });
  }
  const cookie = await createSession(student.id);
  return Response.json({ user: { id: student.id, username: student.username, displayName: student.displayName } }, { headers: { "set-cookie": cookie } });
}
