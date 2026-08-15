# MiniMax References Manager

One node that manages every reference for **MiniMax H3 Reference to Video**, writes the prompt for you, and saves the whole setup to a file you can carry between installs.

![The MiniMax References Manager node](assets/node.png)

## Features

- **Upload instead of wiring.** Drop in images, videos and audio through the node's own UI. Preview them, play them, delete them, reorder nothing. No loader nodes, no links.
- **20 outputs, wired once.** Connect the 18 reference sockets plus `prompt` into `MiniMax H3 Reference to Video` and save the workflow. Change your references as often as you like, the graph never changes.
- **Auto prompting.** A multimodal model over OpenRouter looks at your references, reads your direction text, and writes a full MiniMax H3 prompt in the exact six-section format the model expects.
- **Two registers.** `standard` writes a scene. `replacement` swaps one object or character in a reference video for the thing in a reference image. `auto` lets a cheap classifier pick.
- **Portable configs.** **Save config** downloads a JSON file to your machine. **Load config** reads it back on any install, on any pod, and restores your direction text, model, reasoning effort and reference list.
- **The tags are on the tiles.** Every asset shows the label MiniMax will actually give it: `<Picture 2>`, `<Video 1>`, `<Audio 1>`. What you see is what you address in the prompt.
- **Video soundtracks come along.** A video's audio track is extracted and sent as its own reference by default. Toggle it off per video.
- **A `debug` output.** The exact payload that went to the model: every setting, your direction, the target format, the reference manifest, each media part. Wire it into any text preview node.
- **Prompt passthrough.** Turn `use_openrouter` off and your direction text goes straight to the `prompt` output with no API call.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Hearmeman24/ComfyUI-MiniMaxRefPack
pip install -r ComfyUI-MiniMaxRefPack/requirements.txt
```

Restart ComfyUI.

## Example workflow

A complete Reference-to-Video graph ships with the pack: **Workflow → Browse Templates → ComfyUI-MiniMaxRefPack**, or drag `example_workflows/MiniMax R2V - Auto Prompting + Reference Manager.json` onto the canvas.

## OpenRouter key

Precedence: the node's `openrouter_api_key` box, then `OPENROUTER_API_KEY`, then `LLM_KEY`.

The model dropdown lists only models that accept text, images, audio and video, and defaults to `google/gemini-3-flash-preview`. A key typed into the node is saved inside the workflow JSON, so use the environment variable if you share workflows.

## Settings

| Setting | What it does |
| --- | --- |
| `job_type` | `standard` / `replacement` / `auto`. `auto` only classifies when at least one video and one image are attached, and falls back to `standard` on any failure. |
| `reasoning_effort` | `none` / `low` / `medium` / `high`, default `medium`. Passed to OpenRouter, dropped for models that don't reason. |
| `width` / `height` / `length_seconds` | Told to the model so it composes for the real frame and keeps its cut timestamps inside the real duration. `0` leaves one unspecified. These do not set the output size, `Empty MiniMax H3 AV Latent` does. |
| `use_openrouter` | Off: no API call, `direction` passes through verbatim. |

## The tag rule

1. reference images, in order, become `<Picture 1..n>`
2. then each reference video: if its soundtrack is on, that soundtrack takes the next `<Audio j>` **first**, then the video takes `<Video k>`
3. then standalone audio, continuing the `<Audio j>` count

So a video's soundtrack is `<Audio 1>` even if you added a standalone audio clip before it. `<Video N>` and `<Audio N>` count independently.

## Limits

The model's, not the node's: 9 images, 3 videos, 3 soundtracks, 3 audio clips. Reference videos need at least 5 frames, get trimmed to MiniMax's 17k+5 frame grid, then capped to the length of the video you're generating. Clips are resampled to 24fps on the way in.

## Known issue

The packaged system prompt asks the model to give every label it defines in `subject_definitions` exactly one `retention_analysis` line, while MiniMax's guide says newly invented content gets no retention entry at all. When the model invents something the references didn't supply, it sometimes resolves that by dropping a label or inventing a line for one it never defined. The `debug` socket shows exactly what it was told.

## Licence

MIT.
