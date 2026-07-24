import { userFromRequest } from "../../../localStore";

export async function GET(request: Request) {
  return Response.json({ user: await userFromRequest(request) });
}
