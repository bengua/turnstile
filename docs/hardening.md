# Hardening

Twenty-four scenarios, really triggered on a live machine. Six passes on the
first set, two on the second. Every defect below was found by breaking
something, not by reading code.

The method was blunt: **provoke, observe, fix, run it all again, until the
numbers stop moving.**

---

## What the benchmarks do

Nothing is mocked. `kill -9` really kills the service mid-load. The weights
file is really renamed out from under a loading model. The state file is
really filled with garbage. The disk-full case is a real `chmod 444` on the
corpus.

```
   tests/break-turnstile.py     14 scenarios · the allocator itself
   tests/break-chain.py         10 scenarios · everything around it
```

Both discover their own targets from `/slots`, so they run against any
installation rather than only mine.

---

## The five defects that mattered

They are all the same shape, and that shape is the reason this project
exists:

> **The service survived. It was not telling the truth.**

Not one scenario ever took it down — not the `kill -9` mid-swap, not the
corrupted state, not ten concurrent requests, not the weights vanishing, not
five restarts back to back, not 2 MB of JSON. It stayed up and kept
answering. It just answered things that were not so.

### 1 · A declared slot outlived its container

A container killed after being served left the declaration intact. The probe
saw the slot disappear; the state kept asserting it was serving.

The exact divergence the whole project was built to remove — reintroduced by
the state file itself.

**Fixed:** the probe clears the declaration and counts a divergence.

### 2 · A full disk closed the connection with no message

An unwritable corpus raised inside the request handler, which killed the
handler, which dropped the socket. The service survived; the caller got a
raw cut with no reason.

Losing a judgment pair is tolerable. Lying about why is not.

**Fixed:** `507`, with the reason and the consequence spelled out.

### 3 · Six swaps in 32 seconds, no resistance at all

The allocator obeyed, because obeying is its job. Nothing protected against
a client in a loop: a misconfigured stream, a script in error, a forgotten
test window.

A machine that flaps endlessly is **worse** than a machine that is down. A
clean outage gets noticed and repaired. An oscillation burns power, wears
the cards, and goes unnoticed for days.

**Fixed:** 30 swaps per hour, sliding window. It refuses *change* requests
only — a slot already serving keeps serving.

### 4 · Test data promoted a model to production

The fault-injection benchmark appended 20 000 fictitious pairs at 100 %
agreement. The evaluator read them, computed perfect agreement, and wrote
`"serves": "local"` into the real registry for a question that never
existed.

Nothing distinguished test data from a real decision.

**Fixed:** only lines carrying a known question set are counted. The others
are ignored and reported.

### 5 · The guard answered `allowed=null`

With `responseMode: lastNode` and two parallel branches, the workflow
returned whichever branch finished last — sometimes the shadow branch, which
has no verdict at all.

A guard that says neither yes nor no guards nothing.

**Fixed:** one explicit response node that the four decision branches
converge on, and that the shadow branch never touches.

---

## Two defects found by a question, not a benchmark

### A `both cards` swap killed the CPU models

The condition read `"all" in (slot.card, target.card)`. That is true when
`slot.card == "cpu"` too, because `"all"` was being tested for membership in
a tuple that contained it either way.

Three swaps stopped the two CPU models that had been up for twelve hours —
which is to say, exactly the models meant never to swap, the ones that keep
answering *while* a card is loading.

**Fixed:** CPU slots are skipped explicitly, before the footprint test.

### A kernel thread triggered the watcher

The pattern `reaper` matched `[oom_reaper]`. The watcher concluded a game
was running, permanently. In acting mode it would have freed a card for a
kernel thread, in a loop, forever.

**Propose-only mode is what caught it.** That is the entire argument for
shipping an automaton disarmed.

A second version of the same trap: a command that merely *mentions* the
pattern triggers it — including the watcher's own command line, and
including a here-document that happens to contain the word.

**Fixed:** kernel threads are excluded, and the recommended signal is now
`gpu:`, which reads actual card occupancy and interprets no command line at
all.

---

## The method lesson

Six passes on the first benchmark:

| passes | what they found |
|---|---|
| 1 – 3 | **3 real defects in the system** |
| 4 – 6 | **4 defects in the benchmark itself** |

A legitimate state counted as a failure. An unhandled `None`. A ceiling left
saturated between passes. An error body never read.

**A benchmark that only finds its own defects has finished its job.** That
is the stopping condition — not a target score.

And one more, which changed how I read results:

> All three system defects were found by re-reading the **observation**,
> never by reading the verdict. Pass 1 printed `OK` on all three.

The verdict answers the question you thought to ask. The observation
sometimes shows you the one you did not.

---

## Current state

```
  break-turnstile.py    14 OK · 0 to check · 0 broke
  break-chain.py        10 OK · 0 broke
```

Re-run in full after the multi-vendor hardware refactor: unchanged, 24/24.

That is not a claim that the software is correct. It is a claim that these
twenty-four specific things were tried, on a real machine, and that what
they broke has been fixed.
