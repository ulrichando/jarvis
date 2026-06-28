import { registerBundledSkill } from '../bundledSkills.js'

const SKILL_GENERATOR_PROMPT = `# Skill Generator

You are helping the user create a new reusable skill for Jarvis.

User request: {{args}}

## Your Task

Design and write a skill that matches the user's request. A skill is a named
workflow (a markdown file saved in ~/.claude/plugins/) that tells the model
how to approach a repeatable task.

### Skill format

\`\`\`markdown
---
name: skill-name
description: One-line description of when to use this skill
---

# Skill: Skill Name

## When to use
<describe the trigger>

## Steps
1. Step one
2. Step two
...

## Output
<describe what the skill produces>
\`\`\`

### Instructions

1. Ask the user clarifying questions if the request is ambiguous.
2. Draft the skill in the format above.
3. Offer to save it to ~/.claude/plugins/skills/<name>.md using the Write tool.
4. After saving, confirm with: "Skill '<name>' is ready. Use /skill-name to invoke it."
`

export function registerRunSkillGeneratorSkill(): void {
  registerBundledSkill({
    name: 'run-skill-generator',
    description: 'Generate a new reusable skill from a description',
    aliases: ['skill-gen', 'generate-skill'],
    whenToUse: 'When the user wants to create a new skill or automate a repeatable workflow',
    argumentHint: '<description of the skill to generate>',
    async getPromptForCommand(args) {
      const prompt = SKILL_GENERATOR_PROMPT.replace('{{args}}', args || '(no description given)')
      return [{ type: 'text', text: prompt }]
    },
  })
}
