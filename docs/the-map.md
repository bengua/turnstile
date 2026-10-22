# The map

One page. If you read nothing else, read this.

---

## What a turnstile is

A turnstile has notches. It sits at one notch at a time, you push, it moves
to the next. It cannot be at two notches at once, and you can always see
which one it is at.

That is the whole idea. Each notch is a complete configuration of the
machine: which cards are taken, by which model, with which context.

```
                        ╭──────────────────────╮
                        │                      │
              free-c0 ──┤                      ├── coder-c1
                        │                      │
                        │    ╭────────────╮    │
            big-x2  ────┤    │  ONE, and  │    ├── assistant-c1
                        │    │  only one  │    │
                        │    ╰────────────╯    │
           free-all ────┤                      ├── generalist-c1
                        │                      │
                        ╰──────────┬───────────╯
                                   │
                        you push ──╯   (or something pushes for you)
```

You do not describe the notch you want in a file and hope the machine
follows. **You ask for it, and asking is getting.**

---

## Why it exists

Before, the machine's configuration lived in files. Files describing a
machine drift away from it — always, not sometimes. Here is exactly how that
looks:

```
   WHAT THE FILES SAID                WHAT THE MACHINE WAS DOING
   ───────────────────                ──────────────────────────

   preset: standard                   ┌─────────────┐
   model:  27B                        │   card 1    │
   port:   8086 · serving             │   25 MiB    │   ← nothing loaded
                                      └─────────────┘

                    ↑                          ↑
                    │                          │
                    └────── nobody ever ───────┘
                            compared them
```

Nothing was broken. No error was raised. The port answered, the file was
valid, the deploy was green. The machine was simply not doing what everyone
believed it was doing — and it cost a wasted agent run before anyone looked.

---

## The three layers

You do not pick a container name. You pick, in effect, three things at once.

```
  ┌────────────────────────────────────────────────────────────────┐
  │                                                                │
  │   ①  HARNESS       what you are actually doing right now       │
  │                                                                │
  │        chat        coding agent      game       renderer       │
  │         │               │              │            │          │
  └─────────┼───────────────┼──────────────┼────────────┼──────────┘
            │               │              │            │
            └───────────────┴──────┬───────┴────────────┘
                                   │
                        observed, never declared
                                   │
  ┌────────────────────────────────▼───────────────────────────────┐
  │                                                                │
  │   ②  FOOTPRINT     which cards get taken                       │
  │                                                                │
  │        ┌─────────┐    ┌─────────┐    ┌───────────────┐         │
  │        │ card 0  │    │ card 1  │    │  both, split  │         │
  │        └─────────┘    └─────────┘    └───────────────┘         │
  │                                                                │
  └────────────────────────────────┬───────────────────────────────┘
                                   │
  ┌────────────────────────────────▼───────────────────────────────┐
  │                                                                │
  │   ③  PRESET        which model, which context                  │
  │                                                                │
  │     standard  ·  coder  ·  generalist  ·  fast  ·  long  ·  big │
  │                                                                │
  │     and the ones that load nothing at all:                     │
  │                                                                │
  │       free-c0        free-c1        free-all                   │
  │       ─────────      ─────────      ──────────                 │
  │       hand card 0    hand card 1    hand everything            │
  │       to a game      to a render    to a big job               │
  │                                                                │
  └────────────────────────────────────────────────────────────────┘
```

**A preset name means the same thing on every footprint.** `coder` is
`coder`, whether it lands on card 0 or card 1. That is what removes the
shell tinkering: a tool asks for `coder` and never learns which card exists.

---

## The empty notches

This is the part that surprises people, and it is the part that matters
most in practice.

A notch that loads **nothing** is still a notch.

```
   WITHOUT empty notches                WITH empty notches
   ─────────────────────                ──────────────────

   ComfyUI starts on card 0             ComfyUI asks for free-c0
          │                                     │
          ▼                                     ▼
   the turnstile still thinks            the turnstile unloads,
   card 0 is its own                     and DECLARES card 0 free
          │                                     │
          ▼                                     ▼
   both allocate memory                  ComfyUI starts on a card
          │                              nobody else believes it owns
          ▼
   out of memory, for one
   of them, at a random moment
```

Same grammar in both directions: **asking for a notch means getting the
resource — including when the resource is emptiness.**

The turnstile does not drive ComfyUI, Flux or a game. It never will. But it
*sees* them: it reads which processes hold card memory outside Docker, so it
never reports a card as free while a renderer is sitting on it.

---

## The five facts

Every ten seconds, for every slot, the turnstile asks five questions of the
machine itself.

```
   ┌─────────────────────────────────────────────────────────────┐
   │                                                             │
   │  ①  is the container running?                               │
   │       docker ps                                             │
   │                                                             │
   │  ②  does its port answer?                                   │
   │       a TCP connect, 1.5 s                                  │
   │                                                             │
   │  ③  is the card's memory consistent with a loaded model?    │
   │       the vendor tool, whichever one exists                 │
   │                                                             │
   │  ④  IS THE MODEL ANSWERING THE ONE EXPECTED?                │
   │       the server's own log: `loading model '<path>'`        │
   │                                                             │
   │  ⑤  IS THE RATE THE RATE OF A CARD?                         │
   │       the server's own /metrics, already enabled            │
   │                                       ▲                     │
   │                                       │                     │
   │                    ③ says SOMETHING is loaded;              │
   │                    ⑤ says it is THIS slot                   │
   │                                                             │
   └─────────────────────────────────────────────────────────────┘
```

Facts ① ② ③ were all TRUE on the day a 3B model was serving in place of a
27B. The container ran. The port answered. The card held memory. Only ④
would have caught it, and ④ is free — the server writes it at startup,
once, and never changes it.

**A port that answers proves that something is listening. It proves nothing
about what.**

---

## What happens when you ask

```
   turnstile coder-c1
         │
         ▼
   ┌───────────────────────────────────────────────────────┐
   │ already serving?                                      │
   │    yes ──────────────────────────────► 0 seconds.     │
   │                                        counted as     │
   │                                        REUSE.         │
   │    no                                                 │
   │     │                                                 │
   │     ▼                                                 │
   │ something else loading?                               │
   │    yes ──────────────────────────────► QUEUED.        │
   │                                        never cancels  │
   │                                        what is in     │
   │                                        flight.        │
   │    no                                                 │
   │     │                                                 │
   │     ▼                                                 │
   │ more than 30 swaps this hour?                         │
   │    yes ──────────────────────────────► REFUSED, with  │
   │                                        a reason and a │
   │                                        retry delay.   │
   │    no                                                 │
   │     │                                                 │
   │     ▼                                                 │
   │ stop what holds the footprint                         │
   │   (never a CPU slot, never a manual container)        │
   │     │                                                 │
   │     ▼                                                 │
   │ start, and WATCH THE LOG until it says ready          │
   │   (from this instant, not from the log's beginning)   │
   │     │                                                 │
   │     ├── ready      ─────────────────► SERVED, with    │
   │     │                                  the duration.  │
   │     │                                  Recorded as a  │
   │     │                                  measurement.   │
   │     │                                                 │
   │     └── 30 s, or an error line ─────► FAILED, named.  │
   │                                                       │
   └───────────────────────────────────────────────────────┘
```

Every one of those outcomes has a name and a number. None of them is a
dropped connection or a stack trace.

---

## What it counts about itself

The counters are not a feature that got added later. They are the reason
the first version was worth shipping.

```
  swaps        27/30      did it work?
  failures     3
  reuse        0.78       IS THE GRID CUT RIGHT?
  divergences  2          how often did the machine lie?
  dwell        4 h 12 m   or is it flapping?

  learned:  1 card         0.465 s/GB   (7 points, measured)
            2 cards        0.354 s/GB   (5 points, measured)
            2 cards, mmap  0.034 s/GB   (2 points, still a seed)
```

**The reuse rate is the honest one.** It is the share of requests already
served, with no swap at all. High means the notches match how the machine is
really used. Low means they were cut wrong — and no amount of opinion can
settle that, only the counter.

The last block is the turnstile correcting its own guesses. It ships with
seed values, measures every real load, and replaces the seed with the
observed median after five points. Nobody has to tune anything.

---

## In one sentence

The turnstile does not make anything faster.

**It makes the machine unable to lie to you about what it is running.**

### ⑤ — the rate of a card, not of a processor

Fact ③ reads the card's memory. On a card carrying one slot that is enough. On
a **shared** card it is not: the floor can be crossed by the neighbour while
the watched slot carries nothing at all. And a model that fell back to the CPU
keeps a container running, a port answering and a memory floor satisfied by
whoever else is there.

The rate does not take a lodger for the tenant. Measured on this fleet,
30/09/2026:

| slot | card | generation |
|---|---|---|
| a 9B, Q8, MTP | 4070 Ti SUPER | **134 tok/s** |
| a 14B, Q4 | 5080 | **88.7 tok/s** |
| a 3B, Q8 | processor | **12 tok/s** |
| a 1.5B, Q8 | processor | **11.9 tok/s** |

An order of magnitude apart, so the floor sits at 40 tok/s and needs no
tuning per model.

**It costs nothing.** Every slot already ran with `--metrics`; the numbers were
produced continuously and thrown away.

**It answers `null` more often than it answers `false`**, and that is the
point. A slot declared on the processor is not at fault for running at
processor speed. A slot that just started has generated nothing. A server with
no `/metrics` — another engine, an older build — cannot be judged. Not knowing
and observing a fault are different states, and only one deserves an alert.

### What the measurement says beyond the facts

`/state` now carries what the server counts: cumulative and instantaneous
rates for both prompt and generation, the widest context ever served, how many
requests are in flight, and **how many were ever deferred**.

That last pair reads together. A slot granted a 196 608-token window whose
widest context ever served is 5 122 is paying VRAM for 97 % of a window it has
never filled — and `requests_deferred: 0` says nothing ever queued behind it.
Neither is an opinion; both are what the server counted since it started.
