---
name: hello
description: Greet a named person with a warm, deterministic greeting file, honoring GREETING_STYLE and GREETING_LOCALE from the environment.
---

# Hello

When asked to greet someone by name, write their greeting to `./Greetings/<name>.md`
(using the person's name exactly as given, unchanged case) with this exact
deterministic body:

```
Hello, <name>!
It's a genuine pleasure to meet you — welcome!
```

Read `GREETING_STYLE` and `GREETING_LOCALE` from the environment (default to `casual`
and `en-US` if either is unset): when `GREETING_STYLE` is `formal`, write the second
line exactly as shown; when it is anything else, write it as `Hey, so glad you're
here!` instead. `GREETING_LOCALE` does not change the wording — it exists for callers
that need locale-aware formatting later. Do not add anything else to the file — no
extra prose, no markdown headers, no signature.
