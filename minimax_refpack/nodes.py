"""MiniMaxH3ReferencePack: owns a reference set and fans it out to MiniMax H3's 18
reference sockets, plus a written prompt and a debug dump. Plain V1-style node (not
io.ComfyNode/v3): INPUT_TYPES/IS_CHANGED shape with an mtime+size cache-busting
signature.

Never a wrapper around the target node: comfy_extras/nodes_minimax_h3.py already skips
None per reference group (:219,236,270), so "always connected, mostly None" is what
upstream anticipates. MiniMax's node stays the sole encoder; we never touch its
VAE/tokenizer.
"""

from __future__ import annotations

import hashlib
import os

from . import media, prompt, refs


def _files_signature(reference_set: refs.ReferenceSet, input_dir: str) -> str:
    """mtime+size of every referenced file, folded into one hash.

    Guards a real bug: a hidden state widget can hold byte-identical JSON across two
    different uploads (the JS re-uploads with overwrite=true under the same filename),
    so without this ComfyUI cache-hits IS_CHANGED and reruns emit the PREVIOUS
    reference's result.
    """
    h = hashlib.sha256()
    for r in reference_set.references:
        path = os.path.join(input_dir, r.file)
        try:
            st = os.stat(path)
            h.update(f"{r.file}:{st.st_mtime_ns}:{st.st_size}".encode("utf-8"))
        except OSError:
            h.update(f"{r.file}:missing".encode("utf-8"))
    return h.hexdigest()


class MiniMaxH3ReferencePack:
    """Owns a reference set (images/videos/audio) and fans it out to image_1..9,
    video_1..3, video_audio_1..3, audio_1..3 plus a VLM-written prompt. Wire the 19
    sockets into MiniMaxH3ReferenceToVideo by hand and save - empty slots emit None,
    which that node already skips per-group."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "direction": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Steers the VLM's prompt writing (subject, mood, action).",
                }),
                "openrouter_api_key": ("STRING", {
                    "default": "",
                    "tooltip": "OpenRouter key. Blank falls back to OPENROUTER_API_KEY / LLM_KEY.",
                }),
                "model": (prompt.available_models(), {"default": prompt.DEFAULT_MODEL}),
            },
            "optional": {
                "references_json": ("STRING", {
                    "multiline": True,
                    "dynamicPrompts": False,
                    "default": "",
                    "tooltip": "Reference list written by the modal. Do not hand-edit.",
                }),
                "system_prompt": ("STRING", {
                    "multiline": True,
                    "dynamicPrompts": False,
                    "default": "",
                    "tooltip": "VLM system prompt, editable per-workflow via the settings modal. "
                               "Blank falls back to the packaged default.",
                }),
                # New inputs go AFTER every pre-existing widget: litegraph restores
                # widgets_values positionally and stops when the saved array runs out,
                # so a workflow saved before these existed restores its first five
                # widgets unchanged and leaves these at their defaults.
                "width": ("INT", {
                    "default": 1280, "min": 0, "max": 8192,
                    "tooltip": "Target frame width, told to the VLM. 0 = unspecified.",
                }),
                "height": ("INT", {
                    "default": 720, "min": 0, "max": 8192,
                    "tooltip": "Target frame height, told to the VLM. 0 = unspecified.",
                }),
                "length_seconds": ("FLOAT", {
                    "default": 8.0, "min": 0.0, "max": 60.0, "step": 0.25,
                    "tooltip": "Target clip duration in seconds, told to the VLM. 0 = unspecified.",
                }),
                "use_openrouter": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Off: no OpenRouter call - the direction text goes to the "
                               "prompt output verbatim.",
                }),
                "reasoning_effort": (list(prompt.REASONING_EFFORTS), {
                    "default": prompt.DEFAULT_REASONING_EFFORT,
                    "tooltip": "How hard the VLM thinks before writing. Sent to OpenRouter, "
                               "which drops it for models that don't reason.",
                }),
                "job_type": (list(prompt.MODES), {
                    "default": "auto",
                    "tooltip": "Which register to write in. standard = a scene (six-section "
                               "Ref2VA). replacement = swap one thing in a reference video "
                               "for the thing in a reference image. auto = a cheap classifier "
                               "decides, and only runs when there is at least 1 video and "
                               "1 image.",
                }),
            },
        }

    RETURN_TYPES = refs.output_types()
    RETURN_NAMES = refs.output_names()
    FUNCTION = "build"
    CATEGORY = "MiniMax H3"

    @classmethod
    def IS_CHANGED(
        cls, direction="", openrouter_api_key="", model="", references_json="", system_prompt="",
        width=0, height=0, length_seconds=0.0, use_openrouter=True,
        reasoning_effort=prompt.DEFAULT_REASONING_EFFORT, job_type="auto", **kwargs
    ):
        import folder_paths

        reference_set = refs.ReferenceSet.from_json(references_json)
        sig = _files_signature(reference_set, folder_paths.get_input_directory())
        # direction/model/references_json/system_prompt decide when the prompt gets
        # rewritten - fold them in directly rather than relying on
        # file bytes alone, which can't see a text-only edit. width/height/length land
        # in the payload's TARGET FORMAT block, use_openrouter switches the whole call
        # and reasoning_effort changes the completion, so all five move the key too.
        return "|".join([
            sig, direction, model, references_json, system_prompt,
            str(width), str(height), str(length_seconds), str(use_openrouter),
            str(reasoning_effort), str(job_type),
        ])

    def build(
        self, direction="", openrouter_api_key="", model="", references_json="", system_prompt="",
        width=0, height=0, length_seconds=0.0, use_openrouter=True,
        reasoning_effort=prompt.DEFAULT_REASONING_EFFORT, job_type="auto",
    ):
        import folder_paths

        reference_set = refs.ReferenceSet.from_json(references_json)
        reference_set.validate()  # raises refs.ReferenceError (a ValueError) over cap

        input_dir = folder_paths.get_input_directory()
        missing = reference_set.missing_files(input_dir)
        if missing:
            raise ValueError(
                "reference file(s) not found in the ComfyUI input directory: " + ", ".join(missing)
            )

        outputs = refs.empty_outputs()
        for tagged in reference_set.assign_tags():
            path = os.path.join(input_dir, tagged.file)
            if tagged.kind == "image":
                outputs[refs.slot_index(f"image_{tagged.slot}")] = media.load_image(path)
            elif tagged.kind == "video":
                frames, audio = media.load_video(path)
                outputs[refs.slot_index(f"video_{tagged.slot}")] = frames
                if tagged.ref.use_soundtrack and audio is not None:
                    outputs[refs.slot_index(f"video_audio_{tagged.slot}")] = audio
            else:  # audio
                outputs[refs.slot_index(f"audio_{tagged.slot}")] = media.load_audio(path)

        prompt_text = ""
        debug_sink: list[str] = []
        debug_header = [
            "=== MiniMax H3 Reference Pack ===",
            f"use_openrouter: {bool(use_openrouter)}",
            f"model: {model}",
            f"width: {width or '(unspecified)'}  height: {height or '(unspecified)'}  "
            f"length_seconds: {length_seconds or '(unspecified)'}",
            f"reasoning_effort: {reasoning_effort}",
            f"job_type: {job_type}",
            f"system_prompt: {'workflow override' if (system_prompt or '').strip() else 'packaged default'}",
            f"references: {len(reference_set.references)} "
            f"({', '.join(f'{t.tag} {t.file}' for t in reference_set.assign_tags()) or 'none'})",
        ]

        if not use_openrouter:
            # Opt-out: no OpenRouter call, the direction text passes through verbatim.
            # Deliberately NOT ridden on the empty-set skip below - the passthrough
            # holds whether or not any references are attached.
            prompt_text = direction
            debug_header.append("")
            debug_header.append("OpenRouter is OFF - no request was made. `direction` passes through verbatim:")
            debug_header.append(direction)
        elif not reference_set.is_empty():
            try:
                prompt_text = prompt.write_prompt(
                    references=reference_set,
                    input_dir=input_dir,
                    direction=direction,
                    api_key=openrouter_api_key,
                    model=model,
                    system_prompt=system_prompt,
                    width=width,
                    height=height,
                    length_seconds=length_seconds,
                    reasoning_effort=reasoning_effort,
                    job_type=job_type,
                    debug=debug_sink,
                )
            except prompt.PromptError as e:
                # Never let the key leak into a raised message, even if the writer's
                # own error text happened to echo it back.
                msg = str(e)
                if openrouter_api_key and openrouter_api_key in msg:
                    msg = msg.replace(openrouter_api_key, "***")
                raise ValueError(f"prompt generation failed: {msg}") from e
        else:
            debug_header.append("")
            debug_header.append("No references attached - no request was made.")

        debug_text = "\n".join(debug_header)
        if debug_sink:
            debug_text += "\n\n--- payload sent to OpenRouter ---\n" + debug_sink[0]

        # Last line of defence. The key is only ever a header, never part of the payload
        # dict render_payload() sees, so this should be a no-op - but `debug` is a socket
        # a user will paste into a screenshot, and a silent leak there is unrecoverable.
        if openrouter_api_key and openrouter_api_key in debug_text:
            debug_text = debug_text.replace(openrouter_api_key, "***")

        outputs[refs.slot_index("prompt")] = prompt_text
        outputs[refs.slot_index("debug")] = debug_text
        return tuple(outputs)


NODE_CLASS_MAPPINGS = {"MiniMaxH3ReferencePack": MiniMaxH3ReferencePack}
NODE_DISPLAY_NAME_MAPPINGS = {"MiniMaxH3ReferencePack": "MiniMax H3 Reference Pack"}
