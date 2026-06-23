import { z } from "zod";

// A baseURL may be a valid URL, "" or null (both clear the stored value).
// Validates SHAPE only (rejects non-URL strings); does not restrict the host.
export const baseURLSchema = z
  .union([z.string().url(), z.literal(""), z.null()])
  .optional();
