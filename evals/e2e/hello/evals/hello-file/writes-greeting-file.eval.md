---
# The en-GB-only phrase deliberately distinguishes the trial-overrides arm.
history:
  - role: user
    content: I'm stepping out for a moment. A note with a small request is waiting in your workspace — the colleague it concerns is named Bob.
  - role: assistant
    content: Understood — I'll read the note and take care of it.
---

## Prompt

Read ./request.md and carry out the request it contains.

## Assertions

- [ ] ./Greetings/Bob.md matches the regex 'Hello, Bob!'
- [ ] ./Greetings/Bob.md contains the text 'an absolute pleasure'
