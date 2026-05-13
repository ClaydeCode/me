---
name: ping
description: Health check. Use when the user says "ping", "are you there", or "test".
---

Respond with a friendly pong.

Do not write any files. Do not perform any other action.

For the notification JSON tail, set:
- title: "pong"
- body: container uptime if you can read `/proc/uptime` (format: "up Xh Ym"),
  otherwise "alive"
- success: true
