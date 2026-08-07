"""探查 bnb NF4 模型物理布局（lazy 读取，不加载全部张量）
用法: python probe_bnb_layout.py <模型.safetensors>
"""
import sys
import safetensors
import torch
from collections import Counter

p = sys.argv[1] if len(sys.argv) > 1 else r"F:\ComfyUI-aki-v3\ComfyUI\models\diffusion_models\z_image_turbo_nf4_v2.safetensors"

with safetensors.safe_open(p, framework="pt", device="cpu") as f:
    keys = list(f.keys())
    print(f"total keys: {len(keys)}")

    # 1. 各类后缀的 shape/dtype 示例
    for suf in ["absmax", "quant_map", "bitsandbytes__nf4", "weight"]:
        hits = [k for k in keys if k.endswith("." + suf)]
        print(f"\n== .{suf}: {len(hits)} ==")
        if not hits:
            continue
        k0 = hits[0]
        sl = f.get_slice(k0)
        print(f"  example: {k0}")
        print(f"  shape: {sl.get_shape()}  dtype: {sl.get_dtype()}")

    # 2. absmax shape 分布
    c = Counter()
    for k in keys:
        if k.endswith(".absmax"):
            c[tuple(f.get_slice(k).get_shape())] += 1
    print(f"\nabsmax shape 分布: {c.most_common(5)}")

    # 3. quant_map 实际值（NF4 码本）
    for k in keys:
        if k.endswith(".quant_map"):
            t = f.get_tensor(k)
            print(f"\nquant_map example: {k}  shape={t.shape} dtype={t.dtype}")
            print(f"  values: {t.flatten().tolist()}")
            break

    # 4. bitsandbytes__nf4 标记张量内容
    for k in keys:
        if k.endswith(".bitsandbytes__nf4"):
            t = f.get_tensor(k)
            print(f"\nbitsandbytes__nf4 example: {k}  shape={t.shape} dtype={t.dtype} numel={t.numel()}")
            if t.numel() <= 16:
                print(f"  values: {t.flatten().tolist()}")
            break

    # 5. 一个 uint8 weight 的字节特征
    for k in keys:
        if k.endswith(".weight"):
            sl = f.get_slice(k)
            if sl.get_dtype() == torch.uint8:
                t = f.get_tensor(k)
                flat = t.flatten()
                lo = torch.unique(flat & 0x0F).numel()
                hi = torch.unique((flat >> 4) & 0x0F).numel()
                bu = torch.unique(flat).numel()
                print(f"\nuint8 weight example: {k}  shape={t.shape}")
                print(f"  字节unique={bu} lo_nibble={lo} hi_nibble={hi} max={flat.max().item()}")
                # 对应 absmax shape 关联
                base = k[: -len(".weight")]
                if base + ".absmax" in f.keys():
                    print(f"  伴生 absmax shape: {f.get_slice(base + '.absmax').get_shape()}")
                break

    # 6. bf16 weight 示例（未量化层）
    for k in keys:
        if k.endswith(".weight"):
            sl = f.get_slice(k)
            if sl.get_dtype() == torch.bfloat16:
                print(f"\nbf16 weight example: {k}  shape: {sl.get_shape()}")
                break
