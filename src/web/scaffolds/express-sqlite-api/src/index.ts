import express from "express";
import { db, init } from "./db";

init();

const app = express();
app.use(express.json());

app.get("/", (_req, res) => {
  res.json({ ok: true, message: "JARVIS Express + SQLite scaffold" });
});

app.get("/api/health", (_req, res) => {
  const row = db.prepare("SELECT 1 as up").get() as { up: number };
  res.json({ ok: row.up === 1, db: "sqlite" });
});

const port = Number(process.env.PORT ?? 5173);
const host = process.env.HOST ?? "0.0.0.0";
app.listen(port, host, () => {
  console.log(`listening on http://${host}:${port}`);
});
