import { env } from "cloudflare:workers";
import { ensureLocalSchema, one, userFromRequest } from "../../localStore";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const user = await userFromRequest(request);
  if (!user) return Response.json({ error: "unauthorized" }, { status: 401 });
  await ensureLocalSchema();
  const row = await one<{ stateJson: string }>(env.DB.prepare("SELECT state_json AS stateJson FROM student_learning_state WHERE user_id = ?").bind(user.id));
  if (!row) return Response.json({ state: null });
  try { return Response.json({ state: JSON.parse(row.stateJson) }); }
  catch { return Response.json({ state: null }); }
}

export async function PUT(request: Request) {
  const user = await userFromRequest(request);
  if (!user) return Response.json({ error: "unauthorized" }, { status: 401 });
  const body = await request.json() as { state?: unknown };
  if (!body.state || typeof body.state !== "object") return Response.json({ error: "invalid state" }, { status: 400 });
  const stateJson = JSON.stringify(body.state);
  if (stateJson.length > 500_000) return Response.json({ error: "state too large" }, { status: 413 });
  await ensureLocalSchema();
  await env.DB.prepare(`
    INSERT INTO student_learning_state (user_id, state_json, updated_at)
    VALUES (?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(user_id) DO UPDATE SET state_json = excluded.state_json, updated_at = CURRENT_TIMESTAMP
  `).bind(user.id, stateJson).run();
  return Response.json({ ok: true });
}
