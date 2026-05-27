import { beforeAll, describe, expect, test } from 'bun:test'

// Test-time env so the Provider builder doesn't throw on resolveApiKey.
beforeAll(() => {
  process.env.DEEPSEEK_API_KEY = 'test-deepseek'
  process.env.GROQ_API_KEY = 'test-groq'
  process.env.GOOGLE_API_KEY = 'test-gemini'
  process.env.OPENAI_API_KEY = 'test-openai'
  process.env.KIMI_API_KEY = 'test-kimi'
  process.env.ANTHROPIC_API_KEY = 'test-anthropic'
})

import { convertMessages, contentToOpenAIParts } from './convert.js'
import { getProviderForModel } from './providers.js'

const TINY_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='

describe('contentToOpenAIParts — vision passthrough', () => {
  test('empty content returns empty string', () => {
    expect(contentToOpenAIParts(undefined, true)).toBe('')
    expect(contentToOpenAIParts(null, true)).toBe('')
    expect(contentToOpenAIParts('', true)).toBe('')
  })

  test('plain string passes through unchanged', () => {
    expect(contentToOpenAIParts('hello', true)).toBe('hello')
    expect(contentToOpenAIParts('hello', false)).toBe('hello')
  })

  test('text-only blocks return flattened string regardless of vision', () => {
    const content = [
      { type: 'text', text: 'hello ' },
      { type: 'text', text: 'world' },
    ]
    expect(contentToOpenAIParts(content, true)).toBe('hello world')
    expect(contentToOpenAIParts(content, false)).toBe('hello world')
  })

  test('text + image with supportsVision=true emits ordered parts', () => {
    const content = [
      { type: 'text', text: 'what is in this image?' },
      {
        type: 'image',
        source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
      },
    ]
    const parts = contentToOpenAIParts(content, true)
    expect(Array.isArray(parts)).toBe(true)
    expect(parts).toEqual([
      { type: 'text', text: 'what is in this image?' },
      { type: 'image_url', image_url: { url: `data:image/png;base64,${TINY_PNG_B64}` } },
    ])
  })

  test('text + image with supportsVision=false flattens image to [image] placeholder', () => {
    const content = [
      { type: 'text', text: 'what is this? ' },
      {
        type: 'image',
        source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
      },
    ]
    const out = contentToOpenAIParts(content, false)
    expect(typeof out).toBe('string')
    expect(out).toBe('what is this? [image]')
  })

  test('url-source image emits raw url, not data URI', () => {
    const content = [
      { type: 'text', text: 'caption this:' },
      { type: 'image', source: { type: 'url', url: 'https://example.com/cat.png' } },
    ]
    const parts = contentToOpenAIParts(content, true)
    expect(parts).toEqual([
      { type: 'text', text: 'caption this:' },
      { type: 'image_url', image_url: { url: 'https://example.com/cat.png' } },
    ])
  })

  test('image-only message emits empty text part for providers that need ≥1 text part', () => {
    const content = [
      {
        type: 'image',
        source: { type: 'base64', media_type: 'image/jpeg', data: TINY_PNG_B64 },
      },
    ]
    const parts = contentToOpenAIParts(content, true)
    expect(Array.isArray(parts)).toBe(true)
    expect((parts as any[])[0]).toEqual({ type: 'text', text: '' })
    expect((parts as any[])[1]).toEqual({
      type: 'image_url',
      image_url: { url: `data:image/jpeg;base64,${TINY_PNG_B64}` },
    })
  })

  test('unknown image source falls back to [image] placeholder text part', () => {
    const content = [
      { type: 'text', text: 'mystery:' },
      { type: 'image', source: { type: 'something-weird', data: 'whatever' } },
    ]
    const parts = contentToOpenAIParts(content, true)
    expect(parts).toEqual([
      { type: 'text', text: 'mystery:' },
      { type: 'text', text: '[image]' },
    ])
  })

  test('image source missing entirely falls back to [image]', () => {
    const content = [
      { type: 'text', text: 'broken:' },
      { type: 'image' },
    ]
    const parts = contentToOpenAIParts(content, true)
    expect(parts).toEqual([
      { type: 'text', text: 'broken:' },
      { type: 'text', text: '[image]' },
    ])
  })

  test('multiple images in one message all emit', () => {
    const content = [
      { type: 'text', text: 'two images:' },
      {
        type: 'image',
        source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
      },
      { type: 'image', source: { type: 'url', url: 'https://example.com/b.png' } },
    ]
    const parts = contentToOpenAIParts(content, true) as any[]
    expect(parts).toHaveLength(3)
    expect(parts[0].type).toBe('text')
    expect(parts[1].type).toBe('image_url')
    expect(parts[2].type).toBe('image_url')
    expect(parts[2].image_url.url).toBe('https://example.com/b.png')
  })
})

describe('convertMessages — vision plumbing on user messages', () => {
  test('text-only user message keeps current string-content shape (regression guard)', () => {
    const out = convertMessages(
      [{ role: 'user', content: [{ type: 'text', text: 'hi there' }] }],
      false,
      true,
    )
    expect(out).toEqual([{ role: 'user', content: 'hi there' }])
  })

  test('user message with image emits array content when supportsVision=true', () => {
    const out = convertMessages(
      [
        {
          role: 'user',
          content: [
            { type: 'text', text: 'what is this?' },
            {
              type: 'image',
              source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
            },
          ],
        },
      ],
      false,
      true,
    )
    expect(out).toHaveLength(1)
    expect(out[0].role).toBe('user')
    expect(Array.isArray((out[0] as any).content)).toBe(true)
  })

  test('user message with image flattens to placeholder text when supportsVision=false', () => {
    const out = convertMessages(
      [
        {
          role: 'user',
          content: [
            { type: 'text', text: 'what is this? ' },
            {
              type: 'image',
              source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
            },
          ],
        },
      ],
      false,
      false,
    )
    expect(out).toHaveLength(1)
    expect(out[0]).toEqual({ role: 'user', content: 'what is this? [image]' })
  })

  test('tool_result content always stays as string (OpenAI tool role does not accept image parts)', () => {
    const out = convertMessages(
      [
        {
          role: 'user',
          content: [
            {
              type: 'tool_result',
              tool_use_id: 'toolu_123',
              content: [
                { type: 'text', text: 'screenshot taken' },
                {
                  type: 'image',
                  source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
                },
              ],
            },
          ],
        },
      ],
      false,
      true,
    )
    expect(out).toHaveLength(1)
    expect(out[0].role).toBe('tool')
    // OpenAI's role:tool schema mandates a string content, so image
    // blocks in tool_result must remain a [image] placeholder even
    // when the model otherwise supports vision.
    expect((out[0] as any).content).toBe('screenshot taken[image]')
  })

  test('image-only user message survives (was previously dropped silently)', () => {
    const out = convertMessages(
      [
        {
          role: 'user',
          content: [
            {
              type: 'image',
              source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
            },
          ],
        },
      ],
      false,
      false,
    )
    // Pre-Step-3 behaviour silently dropped image-only messages. Now they
    // survive as a [image] placeholder for non-vision providers.
    expect(out).toHaveLength(1)
    expect(out[0]).toEqual({ role: 'user', content: '[image]' })
  })
})

describe('convertRequest — provider.supportsVision wired through', () => {
  test('gpt-4o provider passes supportsVision=true into convertMessages', () => {
    const p = getProviderForModel('gpt-4o')
    expect(p?.supportsVision).toBe(true)
    // Sanity smoke: convertRequest doesn't throw with image content
    // (the actual conversion is covered by the convertMessages tests
    // above; this is just the wiring check).
    const { convertRequest } = require('./convert.js')
    const out = convertRequest(
      {
        messages: [
          {
            role: 'user',
            content: [
              { type: 'text', text: 'what is this?' },
              {
                type: 'image',
                source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
              },
            ],
          },
        ],
      },
      p,
    )
    const userMsg = out.messages.find((m: any) => m.role === 'user')
    expect(Array.isArray(userMsg.content)).toBe(true)
  })

  test('deepseek-chat provider passes supportsVision=false (image becomes [image])', () => {
    const p = getProviderForModel('deepseek-chat')
    expect(p?.supportsVision).toBe(false)
    const { convertRequest } = require('./convert.js')
    const out = convertRequest(
      {
        messages: [
          {
            role: 'user',
            content: [
              { type: 'text', text: 'no vision here: ' },
              {
                type: 'image',
                source: { type: 'base64', media_type: 'image/png', data: TINY_PNG_B64 },
              },
            ],
          },
        ],
      },
      p,
    )
    const userMsg = out.messages.find((m: any) => m.role === 'user')
    expect(typeof userMsg.content).toBe('string')
    expect(userMsg.content).toBe('no vision here: [image]')
  })
})
