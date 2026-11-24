# GhostTrace

> **Simulated data only.** A fictional toll corridor, fictional registrations and synthetic camera frames drawn by code.
> No real vehicles, number plates or photos. An independent technical exploration, not an official Microsoft product
> and not a Government of India position.

India's barrier-less (MLFF) toll gantries charge a vehicle only when its FASTag and its number plate match.
A vehicle that hides both crosses unidentified: a "ghost". GhostTrace is a test bench for one question:

**Can a ghost be named from other toll data, with so few wrong matches that a human reviewer can safely act?**

It simulates a corridor, matches each ghost against vehicles identified at older boom-barrier plazas, and measures
every answer against the simulation's truth. An optional AI path uses a vision model in Microsoft Foundry to read
synthetic camera frames and compare look-alike vehicles. A person always makes the final decision; nothing in this
project issues a penalty.

## Run it

With Docker or Podman:

```bash
docker compose up --build        # or: podman compose up --build
# open http://localhost:8080      (first start takes about a minute)
```

`podman compose` delegates to an installed Compose provider, such as `podman-compose` or `docker-compose`.

Without a container engine (Python 3.10 or later, standard library only):

```bash
python -m services.pipeline      # simulate, match, tune, decide, report
python -m services.api           # open http://localhost:8080
python -m unittest discover -s tests -v
```

`make up`, `make local` and `make test` do the same.

## Optional AI mode (Microsoft Foundry)

The default demo runs offline. To turn on the two AI buttons, run the API on the host after `az login`.
Authentication is keyless (Microsoft Entra ID); no keys are stored and no credentials go into the container.

You need a Foundry project with a vision-capable chat model deployment. For the explanation button you also
need the prompt agent, created once with `python agents/prompt/create_prompt_agent.py`.

```powershell
python -m pip install -r requirements-foundry.txt
$env:GT_DB = "data/ghosttrace-ai.db"
$env:GT_PORT = "8081"
$env:MODEL_BASE_URL = "https://<your-resource>.services.ai.azure.com/openai/v1/"
$env:MODEL_NAME = "<your-vision-deployment>"
$env:FOUNDRY_AGENT_ENABLED = "1"
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<your-resource>.services.ai.azure.com/api/projects/<your-project>"
python -m services.pipeline
python -m services.api            # open http://localhost:8081
```

- **Run AI vision match.** The app renders a synthetic frame for the ghost and for each time-feasible candidate.
  The vision model reads every frame into a fingerprint: body, colour, plate condition and marks such as stickers,
  dents, stripes and roof racks. A fixed rule compares the fingerprints and names a vehicle only when one candidate
  clearly agrees and none of its marks conflict. The engine's decision is shown next to it and never replaced.
- **Generate AI explanation.** A Foundry prompt agent explains the engine's decision in plain language. Its answer
  is checked against a schema, the engine's decision and the case data; if a check fails, the template is shown.

Measure the AI vision path against simulated truth (one model call per case):

```bash
python -m services.eval.vision_ai --n 30
```

## What you see

| Screen | What it shows |
|---|---|
| Corridor replay | Traffic past two boom-barrier plazas and three MLFF gantries. Ghosts turn red. |
| Case file | One ghost: what the camera saw, the candidate line-up, the engine's reasoning, the synthetic frames and the optional AI result. |
| Reliability dial | Move the time window and the similarity threshold; watch how many ghosts are named and how many names are wrong. |
| Reviewer desk | Evidence, AI result and Confirm / Reject / Need more. Every decision goes to an audit log. |

## First results (simulated)

Four simulated days, about 27,000 crossings a day at the busiest gantry, 3% ghosts, 7,039 test ghosts.
`python -m services.pipeline` regenerates these numbers.

| Method | Vehicles named | Wrong | Named correctly |
|---|---:|---:|---:|
| 10-minute window, same make, model and colour, nearest plaza | 2,569 | 1,034 | 59.8% |
| Adds an image-similarity check, both plazas, 60-minute window | 4,292 | 38 | 99.11% |
| Same, at a pessimistic image-quality assumption | 477 | 30 | 93.71% |

The engine's image similarity is an assumed score, not a measurement.

AI vision match on synthetic frames (30 test ghosts with two or more look-alikes): the AI named 8 vehicles,
6 correctly; 18 went to a person as "several candidates" and 4 stayed unresolved. Both wrong names happened
when the true vehicle was not in the line-up and a look-alike shared its only visible mark.

These numbers show how the method behaves under stated assumptions. They say nothing about real toll-camera footage.

## Design rules

- The matching engine (`services/correlate`) never reads simulation truth. Only the evaluation, the simulator and
  the synthetic camera may.
- Model calls go through one module (`services/common/llm.py`) and every answer is validated against a JSON schema.
- The AI recommends; a person decides. No code path issues a penalty.
- Every screen says "Simulated data".

## Repository map

```text
config/corridor.json      every assumption of the simulation
schemas/                  JSON shapes of API and model answers
services/sim              traffic simulator
services/vision           synthetic camera, simulated similarity, AI vision match
services/correlate        matching engine (never reads truth)
services/eval             evaluation (the only reader of truth)
services/agent            evidence template and grounding check
services/common           config, database, fleet, the single model client
services/api              HTTP API; also serves services/web
services/web              the web app (plain HTML and JavaScript)
agents/                   Foundry prompt-agent instructions, answer schema and setup script
tests/                    unit tests
```

## Source of the problem statement

The Indian Express, Express Special: *India's Highway Ghost Cars: How Vehicles Are Evading The New FASTag Toll System*
(video: https://youtu.be/10NSrXwels0), and reports by Dheeraj Mishra, 10 and 21 November 2026.
All real-world figures here come from them.

## License

MIT. See [LICENSE](LICENSE).
