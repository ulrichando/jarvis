# Jarvis CLI

Jarvis CLI is an AI coding assistant with multi-provider support for DeepSeek, Gemini, Ollama, OpenAI, and Anthropic models.

## Features

- **Multi-provider support**: Switch between different AI providers
- **Plugin system**: Extend functionality with plugins
- **Tool integration**: Built-in tools for coding, file operations, and web search
- **Cross-platform**: Works on Linux, macOS, and Windows

## Quick Start

```bash
# Install dependencies
npm install

# Start the CLI
npm start

# Or run with a specific provider
npm run dev:deepseek
npm run dev:gemini
npm run dev:ollama
npm run dev:openai
```

## Installation

### Global Installation
```bash
npm install -g jarvis-cli
jarvis
```

### Development Setup
```bash
git clone https://github.com/ulrichando/jarvis.git
cd jarvis/src/jarvis-cli
npm install
npm start
```

## Usage

```bash
# Start interactive session
jarvis

# Run with a prompt
jarvis "Help me debug this code"

# Manage plugins
jarvis plugin install <plugin-name>
jarvis plugin list

# Get help
jarvis --help
```

## Configuration

Set API keys in `.env` file:
```
DEEPSEEK_API_KEY=your_key
OPENAI_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
GEMINI_API_KEY=your_key
```

## License

MIT License - see [LICENSE](LICENSE) file for details.
