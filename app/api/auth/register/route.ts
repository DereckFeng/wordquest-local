import { env } from "cloudflare:workers";
import { createSession, ensureLocalSchema, hashPassword, normalizeUsername, one, randomToken } from "../../../localStore";

export async function POST(request: Request) {
  const body = await request.json() as { username?: string; displayName?: string; password?: string };
  const username = normalizeUsername(body.username || "");
  const displayName = (body.displayName || body.username || "").trim();
  const password = body.password || "";
  if (username.length < 2 || username.length > 24 || displayName.length < 1 || displayName.length > 30) {
    return Response.json({ error: "用户名需要 2–24 个字符。" }, { status: 400 });
  }
  if (password.length < 6 || password.length > 72) return Response.json({ error: "密码至少需要 6 位。" }, { status: 400 });
  await ensureLocalSchema();
  const duplicate = await one<{ id: string }>(env.DB.prepare("SELECT id FROM students WHERE username = ?").bind(username));
  if (duplicate) return Response.json({ error: "这个用户名已经被使用。" }, { status: 409 });
  const id = crypto.randomUUID();
  const salt = randomToken(18);
  const passwordHash = await hashPassword(password, salt);
  await env.DB.prepare("INSERT INTO students (id, username, display_name, password_hash, password_salt) VALUES (?, ?, ?, ?, ?)")
    .bind(id, username, displayName, passwordHash, salt).run();
  const cookie = await createSession(id);
  return Response.json({ user: { id, username, displayName } }, { headers: { "set-cookie": cookie } });
}
