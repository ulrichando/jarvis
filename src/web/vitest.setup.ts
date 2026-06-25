import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Ensure DATABASE_URL is set for better-auth initialization in auth.ts.
// Most tests mock the DB or don't use it; setting a dummy URL prevents
// the module-load error for auth-helpers imports.
if (!process.env.DATABASE_URL) {
  process.env.DATABASE_URL =
    "postgresql://test:test@localhost:5432/test_jarvis";
}

afterEach(() => cleanup());

// Tests that need MSW import { server } from "./tests/_msw/server" and
// call server.listen() in their own beforeAll. We don't start it
// globally because most unit tests mock the SDK directly and don't
// need a network interceptor.
