# Orchestrator

A meta-agent that manages multiple concurrent provider-backed coding sessions from a single dashboard.

https://github.com/user-attachments/assets/ebb2bb47-c4e2-4e01-81e5-f0b1c8b1bede

## Provider Support

Current provider matrix:

| Capability | Claude | Codex |
|---|---|---|
| Local workers | Yes | Yes |
| Local brain | Yes | Yes |
| Remote workers | Yes | No |
| Model selection | Yes | Yes |
| Effort selection | Yes | Yes |
| Skip permission prompts | Yes | No |
| Hook automation | Yes | No |
| Skills deployment | Yes | No |
| Brain heartbeat / auto-monitoring | Yes | Yes |
| Quick clear | Yes | Yes |
| Reconnect / auto-reconnect | Yes | No |

Notes:
- Claude is still the full-featured baseline path.
- Codex currently targets strong local brain + local worker support.
- Codex heartbeat is app-managed by Orchestrator rather than provider-native.
- Codex quick clear is implemented as a translated reset flow, not a native slash-command path.
- Unsupported Codex features remain visible in the UI and are disabled with tooltips.


## Download

Download the latest dmg (Mac M-series processor): https://github.com/yudongqiu/orchestrator/releases/latest/download/Orchestrator_aarch64.dmg

Install by simply dragging the app to the applications folder.


## Development
See development.md for details.


## License

Private / Internal Use
