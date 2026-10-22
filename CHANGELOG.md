# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Fixed (4 October 2026)

- **After a game, every slot the release stopped comes back**, not only the declared one: with a model
  on each card, `restore` named the card-1 slot and the card-0 slot stayed off. The release now records
  what it stopped; leaving it starts them again (except on a card a new slot just took), then the guests.

### Added (4 October 2026)

- **A card guest can share its card with named slots**: `guests.map` takes an optional third
  field, `container|card|slot,slot`. Loading one of those slots no longer stops the guest, and the
  guest wakes beside it; any other slot, a slot spanning every card, or a release to a game still
  stops it as before. The house's voice and the light coder now share card 0.

### Fixed
- A slot whose container does not exist is refused with `no_container` before anything is
  stopped. The turnstile starts containers, it does not create them: it used to stop the
  slot holding the card, then fail on `docker start`, leaving the card empty.

### Added
- Card guests (`~/.config/turnstile/guests.map`, `container|card`): a container that uses a
  card only while no slot holds it. The turnstile stops it before a slot loads on its card
  and when the card is released (`free-cN`, `free-all`), and starts it again once the card
  holds nothing. First guest: the house's voice (Whisper + Kyutai TTS) on card 0.

- `TURNSTILE_READ_TOKEN`: a second bearer token that opens the `GET` routes
  and nothing else. Hand it to whatever only reads `/state`; keep
  `TURNSTILE_TOKEN` for what swaps. The authentication is tested for the
  first time — it had no test at all.

## [0.2.0] — 2026-09-30

### Added

- `turnstile-names` — a slot is named after what it carries:
  `<model>-<engine>-<card>`, derived from the container itself (the `-m`
  weights file, the image, the GPU it is given). `check` exits 1 when a
  container's name no longer matches its content. A name that encodes the
  model lies the day the model changes; a derived one cannot.
- `turnstile-litellm` — generates the gateway's model list from the map: one
  entry per slot, named like the slot, no role aliases, no fallback chains.
  A fallback answers under a name that was not asked for.
- `integrations/turnstile-openfox` — points OpenFox at the gateway with the
  map's models and windows (11/12 of the served context), removes local
  providers that bypassed the gateway, and rewrites the file whole.
- **A fifth fact: `throughput_credible`.** Fact ③ reads the card's memory,
  which is enough on a card carrying one slot. On a shared card the floor can
  be crossed by the neighbour while the watched slot carries nothing — and a
  model that fell back to the CPU keeps a container running, a port answering,
  and that floor satisfied by whoever else is there. The rate does not take a
  lodger for the tenant. Measured on one fleet: 134 and 88.7 tok/s on cards
  against 12 and 11.9 on processors, an order of magnitude apart, so the floor
  sits at 40 tok/s and needs no per-model tuning.

  It answers `null` more often than `false`, deliberately: a slot declared on
  the processor is not at fault for running at processor speed, a slot that
  just started has generated nothing, and a server exposing no `/metrics`
  cannot be judged. Not knowing and observing a fault are different states.

- **`version` in `/state`, and a `VERSION` constant.** It exists for a precise
  reason: on 30/09/2026 a tool on this fleet ran for two hours in a
  hand-installed version while the repository carried another, and nothing
  anywhere could say so. A service that cannot announce its version does not
  let you know whether the machine runs what the repository describes.

- **The server's own measurement in `/state`.** Cumulative and instantaneous
  rates for prompt and generation, the widest context ever served, requests in
  flight and requests ever deferred. It costs nothing: every slot already ran
  with `--metrics` and the numbers were being thrown away.

  Read together they answer questions no opinion can. A slot granted a 196 608
  token window whose widest context ever served is 5 122 pays VRAM for 97 % of
  a window it never filled, and `requests_deferred: 0` says nothing ever
  queued behind it.

## [0.1.0] — 2026-09-28

First working version, running on the machine it was written for.

### Added

- `turnstiled` — the allocator. Probes four facts per slot every ten
  seconds (container running, port answering, memory consistent, and the
  identity of the model that actually answers), loads and unloads
  containers, and counts everything it does. Standard library only.
- **Release slots** (`free-c0`, `free-c1`, `free-all`) — slots that load no
  model and hand a footprint back, so a renderer or a game can take a card
  without stealing it.
- A **self-correcting load estimator**: ships with seeds, measures every
  real load, and replaces the seed with the observed median per profile
  after five points.
- `turnstile` — the CLI. Calls and prints; holds no decision logic.
- `turnstile-watch` — asks for a slot based on observed activity, after
  twenty seconds of continuous work. Ships disarmed: it records what it
  would have done until given `--act`.
- `turnstile-record` — one counter snapshot per day, so there is a series
  to look at in a month.
- `turnstile-cascade` — promotes a judgment question from a remote judge to
  a local one at 97 % agreement, demotes below 93 %.
- Vendor abstraction for NVIDIA, AMD and Intel. Only NVIDIA is tested.
- An optional bearer token on the API (`TURNSTILE_TOKEN`).
- `examples/guard-before-run.n8n.json` — a guard workflow reading the real
  state before an agent job is dropped.
- 24 fault-injection scenarios in `fault-injection/`, really triggered.

### Guards, each one added after something went wrong

- A ceiling of 30 swaps per hour: six swaps happened in 32 seconds with no
  resistance at all.
- A declared slot cannot outlive its container: a killed container left the
  state claiming it still served.
- Named refusals instead of dropped connections: a full disk closed the
  socket with no message.
- CPU slots are never stopped: an `all` swap killed the models meant to
  answer *during* a swap.
- Only known question sets are evaluated: a benchmark injected 20 000 fake
  records and promoted a model to production.
