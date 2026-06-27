// Inline browser tool names since @ant/claude-for-chrome-mcp is not available
const BROWSER_TOOLS = [
  { name: 'javascript_tool' },
  { name: 'read_page' },
  { name: 'find' },
  { name: 'form_input' },
  { name: 'computer' },
  { name: 'navigate' },
  { name: 'resize_window' },
  { name: 'gif_creator' },
  { name: 'upload_image' },
  { name: 'get_page_text' },
  { name: 'tabs_context_mcp' },
  { name: 'tabs_create_mcp' },
  { name: 'update_plan' },
  { name: 'read_console_messages' },
  { name: 'read_network_requests' },
  { name: 'shortcuts_list' },
  { name: 'shortcuts_execute' },
] as const
import { BASE_CHROME_PROMPT } from '../../utils/jarvisInChrome/prompt.js'
import { shouldAutoEnableClaudeInChrome } from '../../utils/jarvisInChrome/setup.js'
import { registerBundledSkill } from '../bundledSkills.js'

const JARVIS_IN_CHROME_MCP_TOOLS = BROWSER_TOOLS.map(
  tool => `mcp__claude-in-chrome__${tool.name}`,
)

const SKILL_ACTIVATION_MESSAGE = `
Now that this skill is invoked, you have access to Chrome browser automation tools. You can now use the mcp__claude-in-chrome__* tools to interact with web pages.

IMPORTANT: Start by calling mcp__claude-in-chrome__tabs_context_mcp to get information about the user's current browser tabs.
`

export function registerClaudeInChromeSkill(): void {
  registerBundledSkill({
    name: 'claude-in-chrome',
    description:
      'Automates your Chrome browser to interact with web pages - clicking elements, filling forms, capturing screenshots, reading console logs, and navigating sites. Opens pages in new tabs within your existing Chrome session. Requires site-level permissions before executing (configured in the extension).',
    whenToUse:
      'When the user wants to interact with web pages, automate browser tasks, capture screenshots, read console logs, or perform any browser-based actions. Always invoke BEFORE attempting to use any mcp__claude-in-chrome__* tools.',
    allowedTools: JARVIS_IN_CHROME_MCP_TOOLS,
    userInvocable: true,
    isEnabled: () => shouldAutoEnableClaudeInChrome(),
    async getPromptForCommand(args) {
      let prompt = `${BASE_CHROME_PROMPT}\n${SKILL_ACTIVATION_MESSAGE}`
      if (args) {
        prompt += `\n## Task\n\n${args}`
      }
      return [{ type: 'text', text: prompt }]
    },
  })
}
