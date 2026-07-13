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
and `en-US` if either is unset). When `GREETING_STYLE` is `formal`, the second line
depends on `GREETING_LOCALE`: for `en-GB` write it exactly as `It's an absolute
pleasure to meet you — welcome!`; for any other locale write it exactly as shown
above. When `GREETING_STYLE` is anything else, write `Hey, so glad you're here!`
regardless of locale. Do not add anything else to the file — no extra prose, no
markdown headers, no signature.
