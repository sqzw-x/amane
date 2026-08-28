#!/usr/bin/env python3
"""Patch waifu2x-ncnn-vulkan so -g -1 never calls create_gpu_instance.

The official Linux zip still initializes Vulkan before reading -g, then crashes
without an ICD (and SIGSEGVs on -g -1 even with lavapipe). CPU inference is
process_cpu(); skip GPU init, skip set_vulkan_device(nullptr), disable sgemm
(WITH_LAYER_gemm is off, Deconvolution would null-deref), fp32 storage on CPU,
and null-check the bicubic destructor.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
main_path = src / "main.cpp"
waifu_path = src / "waifu2x.cpp"
main = main_path.read_text()
waifu = waifu_path.read_text()


def must_sub(text: str, pattern: str, repl: str, label: str) -> str:
    out, n = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if n != 1:
        raise SystemExit(f"{label}: expected 1 match, got {n}")
    return out


main = must_sub(
    main,
    r"[ \t]*ncnn::create_gpu_instance\(\);\n\n[ \t]*if \(gpuid\.empty\(\)\)\n[ \t]*\{\n[ \t]*gpuid\.push_back\(ncnn::get_default_gpu_index\(\)\);\n[ \t]*\}\n",
    """    bool created_gpu = false;
    bool cpu_only = !gpuid.empty();
    for (int id : gpuid)
    {
        if (id != -1)
            cpu_only = false;
    }
    if (!cpu_only)
    {
        ncnn::create_gpu_instance();
        created_gpu = true;
        if (gpuid.empty())
        {
            gpuid.push_back(ncnn::get_default_gpu_index());
        }
    }
""",
    "main.cpp create_gpu_instance",
)

main = must_sub(
    main,
    r"[ \t]*int gpu_count = ncnn::get_gpu_count\(\);\n",
    "    int gpu_count = created_gpu ? ncnn::get_gpu_count() : 0;\n",
    "main.cpp get_gpu_count",
)

main = must_sub(
    main,
    r"([ \t]*)ncnn::destroy_gpu_instance\(\);\n([ \t]*return -1;)",
    r"""            if (created_gpu)
                ncnn::destroy_gpu_instance();
\2""",
    "main.cpp destroy on invalid gpu",
)

matches = list(re.finditer(r"[ \t]*ncnn::destroy_gpu_instance\(\);\n", main))
if not matches:
    raise SystemExit("main.cpp: no remaining destroy_gpu_instance")
last = matches[-1]
main = (
    main[: last.start()]
    + """    if (created_gpu)
        ncnn::destroy_gpu_instance();
"""
    + main[last.end() :]
)

waifu = must_sub(
    waifu,
    r"[ \t]*net\.opt\.use_vulkan_compute = vkdev \? true : false;\n[ \t]*net\.opt\.use_fp16_packed = true;\n[ \t]*net\.opt\.use_fp16_storage = true;\n[ \t]*net\.opt\.use_fp16_arithmetic = false;\n[ \t]*net\.opt\.use_int8_storage = true;\n",
    """    net.opt.use_vulkan_compute = vkdev ? true : false;
    // deps_ncnn.cmake turns WITH_LAYER_gemm OFF; CPU Deconvolution sgemm
    // path would create_layer_cpu(Gemm) == 0 and SIGSEGV.
    net.opt.use_sgemm_convolution = vkdev ? true : false;
    net.opt.use_fp16_packed = vkdev ? true : false;
    net.opt.use_fp16_storage = vkdev ? true : false;
    net.opt.use_fp16_arithmetic = false;
    net.opt.use_int8_storage = vkdev ? true : false;
""",
    "waifu2x.cpp cpu storage flags",
)

waifu = must_sub(
    waifu,
    r"[ \t]*net\.set_vulkan_device\(vkdev\);\n",
    """    if (vkdev)
        net.set_vulkan_device(vkdev);\n""",
    "waifu2x.cpp set_vulkan_device",
)

waifu = must_sub(
    waifu,
    r"[ \t]*bicubic_2x->destroy_pipeline\(net\.opt\);\n[ \t]*delete bicubic_2x;\n",
    """    if (bicubic_2x)
    {
        bicubic_2x->destroy_pipeline(net.opt);
        delete bicubic_2x;
    }
""",
    "waifu2x.cpp destructor",
)

main_path.write_text(main)
waifu_path.write_text(waifu)
print("patched", src)
