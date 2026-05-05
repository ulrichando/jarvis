import { setupServer } from "msw/node";

// Shared MSW server for E2E integration tests against the Moonshot
// chat-completions endpoint. Tests register handlers via `server.use(...)`
// in their own beforeEach/it blocks; the lifecycle (listen/resetHandlers/
// close) is owned by the test file's beforeAll/afterEach/afterAll hooks.
export const server = setupServer();
