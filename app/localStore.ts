import { env } from "cloudflare:workers";

export type LocalUser = { id: string; username: string; displayName: string };
type D1Rows<T> = { results?: T[] };

const SESSION_COOKIE = "wordquest_session";
const SESSION_SECONDS = 30 * 24 * 60 * 60;
let schemaPromise: Promise<void> | null = null;

export function ensureLocalSchema() {
  if (!schemaPromise) {
    schemaPromise = env.DB.batch([
      env.DB.prepare(`CREATE TABLE IF NOT EXISTS students (
        id TEXT PRIMARY KEY NOT NULL,
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        password_salt TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`),
      env.DB.prepare(`CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY NOT NULL,
        user_id TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`),
      env.DB.prepare("CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions (user_id)"),
      env.DB.prepare(`CREATE TABLE IF NOT EXISTS student_learning_state (
        user_id TEXT PRIMARY KEY NOT NULL,
        state_json TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`),
      env.DB.prepare(`CREATE TABLE IF NOT EXISTS course_library (
        id TEXT PRIMARY KEY NOT NULL,
        level TEXT NOT NULL,
        title TEXT NOT NULL,
        title_zh TEXT NOT NULL DEFAULT '',
        sentences_json TEXT NOT NULL,
        source_name TEXT NOT NULL DEFAULT 'local import',
        imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )`),
      env.DB.prepare("CREATE INDEX IF NOT EXISTS course_library_level_idx ON course_library (level)"),
    ]).then(() => undefined).catch((error) => { schemaPromise = null; throw error; });
  }
  return schemaPromise;
}

export async function one<T>(statement: D1PreparedStatement): Promise<T | null> {
  const result = await statement.all() as D1Rows<T>;
  return result.results?.[0] ?? null;
}

export function normalizeUsername(value: string) {
  return value.trim().toLocaleLowerCase();
}

function bytesToBase64Url(bytes: Uint8Array) {
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

export function randomToken(byteLength = 32) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return bytesToBase64Url(bytes);
}

export async function hashPassword(password: string, salt: string) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({
    name: "PBKDF2", hash: "SHA-256", salt: new TextEncoder().encode(salt), iterations: 140_000,
  }, key, 256);
  return bytesToBase64Url(new Uint8Array(bits));
}

export function safeEqual(left: string, right: string) {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return difference === 0;
}

function cookieValue(request: Request, name: string) {
  const cookies = request.headers.get("cookie") || "";
  return cookies.split(";").map((item) => item.trim()).find((item) => item.startsWith(`${name}=`))?.slice(name.length + 1) || "";
}

export async function userFromRequest(request: Request): Promise<LocalUser | null> {
  await ensureLocalSchema();
  const token = cookieValue(request, SESSION_COOKIE);
  if (!token) return null;
  const now = Math.floor(Date.now() / 1000);
  return one<LocalUser>(env.DB.prepare(`
    SELECT students.id, students.username, students.display_name AS displayName
    FROM sessions JOIN students ON students.id = sessions.user_id
    WHERE sessions.token = ? AND sessions.expires_at > ?
  `).bind(token, now));
}

export async function createSession(userId: string) {
  await ensureLocalSchema();
  const token = randomToken();
  const expiresAt = Math.floor(Date.now() / 1000) + SESSION_SECONDS;
  await env.DB.prepare("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)")
    .bind(token, userId, expiresAt).run();
  return `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_SECONDS}`;
}

export async function destroySession(request: Request) {
  await ensureLocalSchema();
  const token = cookieValue(request, SESSION_COOKIE);
  if (token) await env.DB.prepare("DELETE FROM sessions WHERE token = ?").bind(token).run();
  return `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`;
}
