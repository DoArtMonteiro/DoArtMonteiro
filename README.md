# Duarte Monteiro

**Industrial Engineering & Management → R&D and innovation consulting → software, automation and AI systems.**

I build practical tools where reproducibility, clear evidence and reliable data matter. My work spans deterministic simulation, local-first agent infrastructure, geospatial data and interactive products.

## Featured engineering

### [Autonomous Systems V&V Lab](https://github.com/WhiteBlindness/autonomous-systems-vv-lab)
A Rust verification laboratory for autonomous ground-vehicle missions. It combines seeded sensor, communication and actuator faults, temporal safety contracts, deterministic replay, bounded campaigns and counterexample minimization. The repository includes a local Mission Control interface backed by a versioned simulation adapter. M7 is merged; the current physical model is 2D, not real vehicle hardware.

**Stack:** Rust, Python, SQLite-compatible artifacts, GitHub Actions. **Evidence:** canonical replay and Linux/Windows verification.

### [WildfireWatch](https://github.com/WhiteBlindness/wildfire-watch)
A [published satellite-observation map](https://wildfire-watch.duartemonteiro.workers.dev) using NASA FIRMS thermal detections. Observations are not presented as confirmed fires. The data pipeline tracks provenance and freshness, retains last-known-good snapshots and separates measured values from estimates and operational claims.

**Stack:** TypeScript, Cloudflare Workers, MapLibre.

### [JARVIS / Donna](https://github.com/WhiteBlindness/jarvis)
A local-first assistant runtime built around a Rust policy and execution core, typed capabilities, audited approvals and OS-isolated workers. The merged desktop milestone includes a Windows Session Agent, Tauri client and local language-model inference without direct tool authority. Daily-driver privacy and lifecycle work remains under review.

**Stack:** Rust, Python, TypeScript, Tauri, SQLite, Windows AppContainer, Linux Landlock/seccomp.

## Published products and other work

- **[Atlas Arcade](https://github.com/WhiteBlindness/atlas-arcade):** [Geography mini-games](https://atlasarcade.app) with deterministic daily challenges, real-world data and a shared game engine.
- **[Murdoku / Alibi](https://github.com/WhiteBlindness/murdoku):** [Logic puzzle demo](https://murdoku-seven.vercel.app) with a TypeScript unique-solution solver, deterministic case generation and a 60-case catalogue. Further gameplay refinements are in development.
- **[OmniQuiz](https://github.com/WhiteBlindness/omniquiz):** [Browser trivia demo](https://omniquiz-nine.vercel.app) with server-side answer data, deterministic scoring and themed game worlds.
- **[Roundcraft](https://github.com/WhiteBlindness/roundcraft):** Counter-Strike 2 tactical decision training with server-side scoring and demo-based case preparation. Human content review precedes a public beta.
- **[CallBrief](https://github.com/WhiteBlindness/callbrief):** Local funding-opportunity research with official-source adapters, provenance and evidence-backed eligibility evaluation. External distribution and commercial reuse are not implied.

## Building now

**Donna/JARVIS:** safer local assistant workflows, supervised desktop operation and privacy-preserving context.

**Autonomous Systems V&V Lab:** Mission Control, deterministic verification and modular simulation interfaces.

## Technologies

Rust · Python · TypeScript · React · Tauri · Next.js · Cloudflare Workers / D1 / KV · SQLite · MapLibre · Playwright · GitHub Actions
