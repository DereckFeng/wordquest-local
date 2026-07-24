import { destroySession } from "../../../localStore";

export async function POST(request: Request) {
  return Response.json({ ok: true }, { headers: { "set-cookie": await destroySession(request) } });
}
