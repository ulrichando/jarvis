import { describe, it, expect } from "vitest";
import { isPrivateOllamaUrl } from "@/lib/ollama";

// Guards the SSRF surface: the /api/ollama/* routes fetch() the resolved base
// URL server-side, so only http(s) to a loopback / RFC1918-private host may
// pass. If this ever loosens, the routes can be pivoted at internal/metadata
// hosts — keep these assertions green.
describe("isPrivateOllamaUrl (SSRF guard)", () => {
  it("allows loopback + RFC1918 over http(s)", () => {
    for (const u of [
      "http://127.0.0.1:11434",
      "http://localhost:11434",
      "http://[::1]:11434",
      "https://127.0.0.1",
      "http://10.0.0.5:11434",
      "http://192.168.1.50:11434",
      "http://172.16.0.9:11434",
      "http://172.31.255.255",
    ]) {
      expect(isPrivateOllamaUrl(u), u).toBe(true);
    }
  });

  it("rejects file://, non-http schemes, metadata, public + bare hosts", () => {
    for (const u of [
      "file:///etc/passwd",
      "gopher://127.0.0.1",
      "ftp://127.0.0.1",
      "http://169.254.169.254/latest/meta-data/", // cloud metadata
      "http://169.254.1.1",
      "http://8.8.8.8",
      "https://evil.example.com",
      "http://ollama.internal:11434", // bare hostname can DNS-resolve anywhere
      "http://172.32.0.1", // just outside 172.16/12
      "http://0.0.0.0:11434",
      "not a url",
      "",
    ]) {
      expect(isPrivateOllamaUrl(u), u).toBe(false);
    }
  });
});
