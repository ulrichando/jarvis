import { describe, expect, test } from "vitest";
import { sanitizeJsonSchema } from "@/lib/mcp/client";

describe("sanitizeJsonSchema (MCP tool schemas)", () => {
  test("drops a malformed `pattern` (the OurCodingKiddos crash) but keeps structure", () => {
    const input = {
      type: "object",
      properties: {
        // A non-string pattern fails the JSON-Schema meta-schema → Anthropic
        // rejects the whole tools array → chat turn aborts. Must be stripped.
        email: { type: "string", pattern: { broken: true }, description: "email" },
        name: { type: "string" },
      },
      required: ["email"],
    };
    const out = sanitizeJsonSchema(input) as any;
    expect(out.properties.email.pattern).toBeUndefined();
    expect(out.properties.email.type).toBe("string");
    expect(out.properties.email.description).toBe("email");
    expect(out.required).toEqual(["email"]);
  });

  test("strips format/$ref/$schema recursively, including in arrays + nested defs", () => {
    const input = {
      $schema: "https://json-schema.org/draft/2020-12/schema",
      type: "object",
      properties: {
        items: {
          type: "array",
          items: { type: "string", format: "uri", pattern: "^x" },
        },
        ref: { $ref: "#/$defs/Thing" },
      },
      anyOf: [{ type: "object", properties: { a: { format: "date" } } }],
    };
    const out = sanitizeJsonSchema(input) as any;
    expect(out.$schema).toBeUndefined();
    expect(out.properties.items.items.format).toBeUndefined();
    expect(out.properties.items.items.pattern).toBeUndefined();
    expect(out.properties.items.items.type).toBe("string");
    expect(out.properties.ref.$ref).toBeUndefined();
    expect(out.anyOf[0].properties.a.format).toBeUndefined();
  });

  test("leaves a clean schema untouched", () => {
    const clean = {
      type: "object",
      properties: { n: { type: "number", enum: [1, 2] } },
      required: ["n"],
    };
    expect(sanitizeJsonSchema(clean)).toEqual(clean);
  });
});
