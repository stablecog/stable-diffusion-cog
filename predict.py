import time
import os
from typing import List

import torch
from diffusers import (
    StableDiffusionPipeline,
)
from cog import BasePredictor, Input, Path

from models.swinir.helpers import get_args_swinir
from models.stable_diffusion.generate import generate
from models.stable_diffusion.constants import SD_MODEL_CHOICES, SD_MODELS, SD_MODEL_CACHE, SD_MODEL_DEFAULT, SD_SCHEDULER_DEFAULT, SD_SCHEDULER_CHOICES
from models.nllb.translate import translate_text
from models.swinir.upscale import upscale

from lingua import LanguageDetectorBuilder


class Predictor(BasePredictor):
    def setup(self):
        print("⏳ Loading Stable Diffusion v1.5 pipelines...")

        self.txt2img = StableDiffusionPipeline.from_pretrained(
            SD_MODELS["Stable Diffusion v1.5"]["id"],
            cache_dir=SD_MODEL_CACHE,
            revision=SD_MODELS["Stable Diffusion v1.5"]["revision"],
            torch_dtype=SD_MODELS["Stable Diffusion v1.5"]["torch_dtype"],
            local_files_only=True,
        )
        self.txt2img_pipe = self.txt2img.to('cuda')
        self.txt2img_pipe.enable_xformers_memory_efficient_attention()
        print(f"✅ Loaded txt2img")

        self.txt2img_alt = None
        self.txt2img_alt_pipe = None
        self.txt2img_alt_name = None

        self.txt2img_alts = {}
        for key in SD_MODELS:
            if key != SD_MODEL_DEFAULT:
                print(f"⏳ Loading model: {key}")
                self.txt2img_alts[key] = StableDiffusionPipeline.from_pretrained(
                    SD_MODELS[key]["id"],
                    cache_dir=SD_MODEL_CACHE,
                    local_files_only=True,
                )
                print(f"✅ Loaded model: {key}")

        # For translation
        self.detect_language = LanguageDetectorBuilder.from_all_languages(
        ).with_preloaded_language_models().build()
        print("✅ Loaded language detector")

        self.swinir_args = get_args_swinir()
        self.device = torch.device('cuda')
        print("✅ Loaded upscaler")

        print("✅ Setup is done!")

    @torch.inference_mode()
    @torch.cuda.amp.autocast()
    def predict(
        self,
        prompt: str = Input(description="Input prompt.", default=""),
        negative_prompt: str = Input(
            description="Input negative prompt.", default=""),
        prompt_flores_200_code: str = Input(
            description="Prompt language code (FLORES-200). It overrides the language auto-detection.", default=None),
        negative_prompt_flores_200_code: str = Input(
            description="Negative prompt language code (FLORES-200). It overrides the language auto-detection.", default=None),
        width: int = Input(
            description="Width of output image.",
            choices=[128, 256, 384, 448, 512, 576,
                     640, 704, 768, 832, 896, 960, 1024],
            default=512,
        ),
        height: int = Input(
            description="Height of output image.",
            choices=[128, 256, 384, 448, 512, 576,
                     640, 704, 768, 832, 896, 960, 1024],
            default=512,
        ),
        num_outputs: int = Input(
            description="Number of images to output. If the NSFW filter is triggered, you may get fewer outputs than this.",
            ge=1,
            le=10,
            default=1
        ),
        num_inference_steps: int = Input(
            description="Number of denoising steps", ge=1, le=500, default=30
        ),
        guidance_scale: float = Input(
            description="Scale for classifier-free guidance", ge=1, le=20, default=7.5
        ),
        scheduler: str = Input(
            default=SD_SCHEDULER_DEFAULT,
            choices=SD_SCHEDULER_CHOICES,
            description="Choose a scheduler.",
        ),
        model: str = Input(
            default=SD_MODEL_DEFAULT,
            choices=SD_MODEL_CHOICES,
            description="Choose a model. Defaults to 'Stable Diffusion v1.5'.",
        ),
        seed: int = Input(
            description="Random seed. Leave blank to randomize the seed.", default=None
        ),
        output_image_ext: str = Input(
            description="Output type of the image. Can be 'png' or 'jpg'.",
            choices=["jpg", "png"],
            default="png",
        ),
        image_u: Path = Input(
            description="Input image for the upscaler (Swinir).", default=None
        ),
        task_u: str = Input(
            default="Real-World Image Super-Resolution-Large",
            choices=[
                'Real-World Image Super-Resolution-Large',
                'Real-World Image Super-Resolution-Medium',
                'Grayscale Image Denoising',
                'Color Image Denoising',
                'JPEG Compression Artifact Reduction'
            ],
            description="Task type for the upscaler (Swinir).",
        ),
        noise_u: int = Input(
            description='Noise level, activated for Grayscale Image Denoising and Color Image Denoising. It is for the upscaler (Swinir). Leave it as default or arbitrary if other tasks are selected.',
            choices=[15, 25, 50],
            default=15,
        ),
        jpeg_u: int = Input(
            description='Scale factor, activated for JPEG Compression Artifact Reduction. It is for the upscaler (Swinir). Leave it as default or arbitrary if other tasks are selected.',
            choices=[10, 20, 30, 40],
            default=40,
        ),
        process_type: str = Input(
            description="Choose a process type. Can be 'generate', 'upscale' or 'generate-and-upscale'. Defaults to 'generate'",
            choices=["generate", "upscale", "generate-and-upscale"],
            default="generate",
        ),
        translator_cog_url: str = Input(
            description="URL of the translator cog. If it's blank, TRANSLATOR_COG_URL environment variable will be used (if it exists).",
            default=None
        ),
    ) -> List[Path]:
        output_paths = []
        if process_type == "generate" or process_type == "generate-and-upscale":
            startTime = time.time()

            if translator_cog_url is None:
                translator_cog_url = os.environ.get("TRANSLATOR_COG_URL", None)

            t_prompt = prompt
            t_negative_prompt = negative_prompt
            if translator_cog_url is not None:
                [t_prompt, t_negative_prompt] = translate_text(
                    prompt,
                    prompt_flores_200_code,
                    negative_prompt,
                    negative_prompt_flores_200_code,
                    translator_cog_url,
                    self.detect_language,
                    "Prompt & Negative Prompt"
                )
            txt2img_pipe = None
            if model != SD_MODEL_DEFAULT:
                if self.txt2img_alt is not None and self.txt2img_alt_name != model:
                    self.txt2img_alt.to("cpu")

                self.txt2img_alt = self.txt2img_alts[model]
                self.txt2img_alt_name = model
                txt2img_pipe = self.txt2img_alt.to("cuda")
                txt2img_pipe.enable_xformers_memory_efficient_attention()
            else:
                txt2img_pipe = self.txt2img_pipe

            print(f'-- Generating with "{model}"... --')
            generate_output_paths = generate(
                t_prompt,
                t_negative_prompt,
                width, height,
                num_outputs,
                num_inference_steps,
                guidance_scale,
                scheduler,
                seed,
                output_image_ext,
                model,
                txt2img_pipe
            )
            output_paths = generate_output_paths
            endTime = time.time()
            print(
                f'-- Generated with "{model}" in: {endTime - startTime} sec. --')

        if process_type == 'upscale' or process_type == 'generate-and-upscale':
            startTime = time.time()
            if process_type == 'upscale':
                upscale_output_path = upscale(
                    self.swinir_args, self.device, task_u, image_u, noise_u, jpeg_u)
                output_paths = [upscale_output_path]
            else:
                upscale_output_paths = []
                for path in output_paths:
                    upscale_output_path = upscale(
                        self.swinir_args,
                        self.device,
                        task_u,
                        path,
                        noise_u,
                        jpeg_u
                    )
                    upscale_output_paths.append(upscale_output_path)
                output_paths = upscale_output_paths
            endTime = time.time()
            print(f"-- Upscaled in: {endTime - startTime} sec. --")

        return output_paths
