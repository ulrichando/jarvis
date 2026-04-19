import { test, expect, beforeAll, afterAll } from "bun:test";

const PORT = 18765;
let server: ReturnType<typeof Bun.serve> | undefined;

beforeAll(() => {
  server = Bun.serve({
    hostname: "127.0.0.1",
    port: PORT,
    fetch(req: Request): Response {
      const url = new URL(req.url);
      if (url.pathname === "/health") return Response.json({ status: "ok" });
      return new Response("not found", { status: 404 });
    },
  });
});

afterAll(() => server?.stop(true));

test("health endpoint returns ok", async () => {
  const res = await fetch(`http://127.0.0.1:${PORT}/health`);
  expect(res.status).toBe(200);
  const body = (await res.json()) as { status: string };
  expect(body.status).toBe("ok");
});
