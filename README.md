# ComfyUI-MiniMaxRefPack

Reference management for ComfyUI's **MiniMax H3 Reference to Video**.

MiniMax H3 takes up to 9 reference images, 3 reference videos, their 3 soundtracks and 3 standalone audio
clips — 18 sockets, each its own link, each fed by its own loader node. Changing a reference set means
rewiring the graph.

This pack replaces all of that with one node.

## What it does

- **20 outputs, always present.** Wire the 18 reference sockets plus `prompt` into
  `MiniMax H3 Reference to Video` once and save the workflow. Slots you aren't using send nothing, and MiniMax
  skips them.
- **A UI instead of links.** Upload, preview, play and delete images, videos and audio without touching the
  graph.
- **It shows you the tags.** Every tile displays the label MiniMax will actually give that asset —
  `<Picture 2>`, `<Video 1>`, `<Audio 1>` — computed by the same rule the model's node uses, so your prompt
  addresses the right thing.
- **Portable configs.** Save the current set — direction text, model, reasoning effort and the reference list —
  as a JSON file on your own machine, and load it back on any install.
- **It writes the prompt.** A multimodal model over OpenRouter sees your references and your direction text,
  and the result comes out of the `prompt` output. Turn `use_openrouter` off and your direction text passes
  through verbatim instead, with no API call.
- **Two registers, one node.** `job_type` picks how the prompt is written: `standard` for a scene, or
  `replacement` for swapping one object or character in a reference video for the thing in a reference image.
  `auto` lets a cheap classifier decide.
- **A `debug` output.** The exact payload that went to the model: every setting, your direction, the target
  format, the reference manifest and each media part (base64 elided). Wire it into any text preview node.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Hearmeman24/ComfyUI-MiniMaxRefPack
pip install -r ComfyUI-MiniMaxRefPack/requirements.txt
```

Restart ComfyUI.

## The OpenRouter key

Order of precedence: the node's `openrouter_api_key` box, then `OPENROUTER_API_KEY`, then `LLM_KEY`.

The model dropdown lists only models that accept text, images, audio and video — 31 of them at time of
writing — and defaults to `google/gemini-3-flash-preview`.

Note that a key typed into the node is saved inside the workflow JSON. If you share workflows, use the
environment variable instead.

## Job types

`standard` writes MiniMax's six-section Ref2VA prompt for a scene: `subject_definitions`, `summary`,
`retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`.

`replacement` is for the case where a reference video is the finished shot and one thing inside it is swapped
for the thing in a reference image, with everything else untouched. It emits the **same six sections**, because
MiniMax documents this as a `video editing` task — the summary opens `[video editing]` followed by
`The target video is an edited version of <Video 1>.` Motion inheritance, integration, optics, camera, physics
and lighting all live inside `detailed_description`.

`auto` (the default) asks a cheap classifier which of the two you meant. It only runs when there is at least
one video **and** one image attached, since a swap is impossible otherwise, and any failure falls back to
`standard` rather than blocking the run.

## Reasoning effort

`reasoning_effort` (`none` / `low` / `medium` / `high`, default `medium`) is passed to OpenRouter, which drops
it for models that don't reason. `medium` measurably raises conformance on the structural rules above, at
roughly 1.8x the cost per call.

## Target format

`width`, `height` and `length_seconds` are told to the model so it composes for the real frame and places its
cut timestamps inside the real duration. Set any of them to `0` to leave it unspecified. They do **not** set
the output size — that's `Empty MiniMax H3 AV Latent`'s job, and if the two disagree the latent wins.

## Configs

**Save config** downloads `minimax-refpack-<name>.json` to your machine; **Load config** reads one back
through a file picker. A config stores the direction text, the model, the reasoning effort and the reference
list — filenames relative to `ComfyUI/input`, not absolute paths, so the same file works on any install.

On load, any file the config names that isn't in this install's input folder is marked on its tile rather than
dropped, so you can see exactly what needs re-uploading. A saved model that's no longer in the dropdown is
reported instead of being silently applied.

## The tag rule

Worth knowing, because it isn't obvious:

1. reference images, in order → `<Picture 1..n>`
2. then each reference video: **if it has a soundtrack, that soundtrack takes the next `<Audio j>` first**,
   then the video takes `<Video k>`
3. then standalone audio, continuing the `<Audio j>` count

So a video's soundtrack is `<Audio 1>` even if you added a standalone audio clip before it. `<Video N>` and
`<Audio N>` are counted independently of each other.

## Limits

They're the model's, not ours: 9 images, 3 videos, 3 soundtracks, 3 audio clips. Reference videos need at
least 5 frames and get trimmed to MiniMax's 17k+5 frame grid, then capped to the length of the video you're
generating. Clips are resampled to 24fps on the way in, because MiniMax reads whatever frames it gets as
24fps.

## Known limitations

The packaged system prompt asks the model to give every label it defines in
`subject_definitions` exactly one `retention_analysis` line. MiniMax's own guide says newly invented content
gets no retention entry at all. When the model invents something the references didn't supply — an environment
written purely from your direction — those two rules pull against each other, and it sometimes resolves the
conflict by dropping a label or inventing a line for one it never defined. Worth an eye on the output; the
`debug` socket shows exactly what the model was told.

## Licence

MIT.
