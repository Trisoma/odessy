# How I created Odessy

## Spider-Man and The Odyssey

Everybody loves Spider-Man. Well, at least I do. I grew up watching Spider-Man and other Marvel movies and series. The difference between then and now is that I'm an undergrad, and I finally have some “superpowers” of my own: a bit of knowledge, and the ability to actually build things.

Assistants like JARVIS and FRIDAY have been a childhood wish of mine for years. Coming back to those stories made me think: why not try building something like that myself? That's where the idea of a voice orchestration platform came from.

Around the same time, Nolan's *The Odyssey* inspired the name. I have to confess: this was the first time I'd heard of the Odyssey, and “Odessy” was actually a typo while I was discussing the project with GPT. GPT pointed it out later, but by then, I kind of liked it. So the typo stayed. Consider it a little watermark from the project's beginnings—and, conveniently, a name that's easier to remember because it's slightly wrong.

## How I built it

The core idea and design came from me, with plenty of discussion with AI along the way. That was especially useful because I'm much more of an idealist than a practical person. Sometimes I need something to tell me, “Okay, that sounds cool, but how would it actually work?”

I planned the STT → LLM → TTS chain, the MCP tools and connections, and the initial security boundaries. Then I built the minimum working prototype with help from GPT-5.4 Codex.

Later, I used GPT-5.5 to study DeepSeek Harness (DSH) as a reference for tool calling, security boundaries and sandboxing, and to help adapt relevant ideas and parts into Odessy's own architecture. The model could propose an action, but Odessy needed to be the one deciding whether it was allowed to run.

Around September, one of my college courses asked us to build a student project. Convenient timing: I already had one I cared about. I brought Odessy into the course and built a small team around it. To be clear, this was never supposed to be just a project for a good GPA. I wanted to keep building it after the course ended.

Then a friend happened to ask whether I had any projects he could join. So, naturally, he was in too!

We released v0.5.0-unstable on October 31 and made the repository public at the same time. Still rough, still plenty to do, but finally something we could put in front of other people.

## What I imagine next

My ambition is probably pretty obvious by now: not just a voice assistant, but a device orchestration agent with mature voice interaction. Yes, the JARVIS-and-FRIDAY idea is still there.

I know it sounds a little ridiculous, but imagine this.

You're working in your workshop with a few agents running in parallel. You're typing to one of them, writing an important prompt or fixing something together. Meanwhile, another agent finishes a benchmark. You want a quick check, but you don't really want to switch windows, find the right conversation and type another message.

So you say: “Hey Odessy, help me check the results of that model benchmark.”

Or take the idea a little further. You've built a Raspberry Pi camera to see who's at the door, and an MCU-controlled door mechanism. Someone knocks while your hands are busy.

“Hey Odessy, show me the door camera.”

Oh, it's my friend.

“Hey Odessy, open the door.”

The system would still need proper authorization and confirmation before a physical action. But that's the kind of interaction I have in mind: your own devices, connected through an assistant you can understand and modify.

**That camera-and-door example is a long-term idea, not something Odessy supports today.** Reliable voice interaction and broader device control are goals we're working toward, not promises about the current prototype.

What excites me is being able to build this without committing to one closed ecosystem. Choose the hardware, models and tools that make sense for you, and connect them yourself. That's the direction I want Odessy to grow in.

I don't know how close we'll get to the childhood version of JARVIS. But building our own slightly misspelled version sounds like a pretty good place to start.
