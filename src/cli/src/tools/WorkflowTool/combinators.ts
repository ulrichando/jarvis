export const MAX_ITEMS = 4096

function assertItemCap(n: number): void {
  if (n > MAX_ITEMS) {
    throw new Error(
      `A single parallel()/pipeline() call accepts at most ${MAX_ITEMS} items (got ${n}).`,
    )
  }
}

// Barrier: await all thunks; a throwing thunk resolves to null; never rejects.
export async function runParallel<T>(
  thunks: Array<() => Promise<T>>,
): Promise<Array<T | null>> {
  assertItemCap(thunks.length)
  return Promise.all(
    thunks.map(async t => {
      try {
        return await t()
      } catch {
        return null
      }
    }),
  )
}

// No-barrier pipeline: each item flows through all stages independently.
// Stage callback receives (prevResult, originalItem, index). A stage that
// throws drops that item to null and skips its remaining stages.
export async function runPipeline(
  items: unknown[],
  ...stages: Array<(prev: unknown, item: unknown, index: number) => Promise<unknown>>
): Promise<Array<unknown | null>> {
  assertItemCap(items.length)
  return Promise.all(
    items.map(async (item, index) => {
      let prev: unknown = item
      for (const stage of stages) {
        try {
          prev = await stage(prev, item, index)
        } catch {
          return null
        }
      }
      return prev
    }),
  )
}
