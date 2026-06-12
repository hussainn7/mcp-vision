# mcp-vision

A local, autonomous AI agent that watches your screen, understands the visual layout, and executes native OS commands (clicking, typing) on your behalf. **No cloud APIs, no subscriptions, and zero data leaving your machine.**

The architecture is built on a simple premise: bridge local vision models with standard OS automation. The pipeline captures a screenshot, processes it through Microsoft's OmniParser to generate a structured map of interactive elements, and feeds that layout to Llama 3.2 Vision via Ollama. The model then decides the next action, executing it through a clean, composable Model Context Protocol (MCP) server.