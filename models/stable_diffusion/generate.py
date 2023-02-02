import os
import torch
from .helpers import make_scheduler
from .constants import SD_MODELS
from cog import Path


def generate(
    prompt,
    negative_prompt,
    prompt_prefix,
    negative_prompt_prefix,
    width,
    height,
    num_outputs,
    inference_steps,
    guidance_scale,
    scheduler,
    seed,
    model,
    txt2img_pipe
):
    if seed is None:
        seed = int.from_bytes(os.urandom(2), "big")
    print(f"Using seed: {seed}")

    extra_kwargs = {}

    if prompt_prefix is not None:
        prompt = f"{prompt_prefix} {prompt}"
    else:
        default_prompt_prefix = SD_MODELS[model].get("prompt_prefix", None)
        if default_prompt_prefix is not None:
            prompt = f"{default_prompt_prefix} {prompt}"

    if negative_prompt_prefix is not None:
        if negative_prompt is None or negative_prompt == "":
            negative_prompt = negative_prompt_prefix
        else:
            negative_prompt = f"{negative_prompt_prefix} {negative_prompt}"
    else:
        default_negative_prompt_prefix = SD_MODELS[model].get(
            "negative_prompt_prefix", None
        )
        if default_negative_prompt_prefix is not None:
            if negative_prompt is None or negative_prompt == "":
                negative_prompt = default_negative_prompt_prefix
            else:
                negative_prompt = f"{default_negative_prompt_prefix} {negative_prompt}"

    print(f"-- Prompt: {prompt} --")
    print(f"-- Negative Prompt: {negative_prompt} --")

    pipe = txt2img_pipe
    pipe.scheduler = make_scheduler(scheduler, pipe.scheduler.config)
    generator = torch.Generator("cuda").manual_seed(seed)
    output = pipe(
        prompt=[prompt] * num_outputs if prompt is not None else None,
        negative_prompt=[negative_prompt] *
        num_outputs if negative_prompt is not None else None,
        width=width,
        height=height,
        guidance_scale=guidance_scale,
        generator=generator,
        inference_steps=inference_steps,
        **extra_kwargs,
    )

    output_paths = []
    nsfw_count = 0

    for i, nsfw_flag in enumerate(output.nsfw_content_detected):
        output_path = f"/tmp/out-{i}.png"
        if nsfw_flag:
            nsfw_count += 1
        else:
            output.images[i].save(output_path)
            output_paths.append(Path(output_path))

    if len(output_paths) == 0:
        raise Exception("All outputs are NSFW.")

    if nsfw_count > 0:
        print(
            f"NSFW content detected in {nsfw_count}/{num_outputs} of the outputs."
        )

    return output_paths
