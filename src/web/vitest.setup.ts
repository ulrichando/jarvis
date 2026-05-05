import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

// Tests that need MSW import { server } from "./tests/_msw/server" and
// call server.listen() in their own beforeAll. We don't start it
// globally because most unit tests mock the SDK directly and don't
// need a network interceptor.
