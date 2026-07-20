// Container health probe target for docker-compose.
export function GET() {
  return Response.json({ status: "ok" });
}
