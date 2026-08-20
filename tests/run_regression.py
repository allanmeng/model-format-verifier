"""MFV 回归测试: 对基准模型跑 check_model.py, 断言判定类型关键词。

用法:
    python run_regression.py                    # 扫描模型目录跑全部内置案例断言
    python run_regression.py <模型文件>          # 单模型: 只输出判定, 不断言

内置案例以「文件名通配 + 期望类型关键词」定义, 在 CASE_DIRS 中搜索。
找不到的案例会跳过并提示 (模型不在本机)。

返回码: 0 = 全部通过/跳过, 1 = 有失败。
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.environ.get("MFV_PYTHON", sys.executable)
SCRIPT = os.path.join(ROOT, "check_model.py")

# 模型搜索根目录 (Allan 本机)
CASE_DIRS = [
    r"F:/ComfyUI-aki-v3/ComfyUI/models/diffusion_models",
    r"F:/ComfyUI-aki-v3/ComfyUI/models/diffusion_models/minimax_music3",
    r"F:/ComfyUI-aki-v3/ComfyUI/models/checkpoints",
    r"F:/ComfyUI-aki-v3/ComfyUI/models/clip",
    r"F:/ComfyUI-aki-v3/ComfyUI/models/text_encoders",
    r"E:/model/int4",
    r"E:/model/H3_diff",
]

# 案例库回归基准: (文件名通配, 期望判定类型关键词)
CASES = [
    ("*z_image_turbo_nf4_v2*.safetensors", "NF4 (bitsandbytes)"),
    ("*z_image_turbo_int8_convrot*.safetensors", "ComfyUI INT8"),
    ("*tint4_torchao*.safetensors", "ComfyUI INT4"),
    ("*XL_pony*.safetensors", "接近未量化"),
    ("*Q4_K*.gguf", "GGUF"),
    ("*Q8_0*.gguf", "GGUF"),
    ("*minimax_music3_dit_int8*.safetensors", "ComfyUI INT8"),
    ("*minimax_h3_fl2va*int8*.safetensors", "ComfyUI INT8"),
    ("*qwen_3_4b_fp4*.safetensors", "ComfyUI FP8"),
    ("*umt5*Q3_K*.gguf", "GGUF"),
]


def find_case(pattern):
    for d in CASE_DIRS:
        hits = glob.glob(os.path.join(d, pattern))
        if hits:
            return hits[0]
    return None


def run_check(path):
    r = subprocess.run([PY, SCRIPT, path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    return r.stdout + r.stderr


def main():
    if len(sys.argv) > 1:
        # 单模型模式: 只输出判定, 不断言
        out = run_check(sys.argv[1])
        for line in out.splitlines():
            if "→ 类型识别" in line or "模型类型" in line:
                print(line)
        return 0

    passed, failed, skipped = 0, 0, 0
    print(f"MFV 回归测试 (脚本: {SCRIPT})")
    print("=" * 60)
    for pattern, expect in CASES:
        path = find_case(pattern)
        if not path:
            print(f"[SKIP] {pattern}  (模型不在本机)")
            skipped += 1
            continue
        try:
            out = run_check(path)
        except subprocess.TimeoutExpired:
            print(f"[FAIL] {os.path.basename(path)}  超时(>900s)")
            failed += 1
            continue
        hit = expect in out
        if hit:
            print(f"[PASS] {os.path.basename(path)}  → {expect}")
            passed += 1
        else:
            # 找实际识别行帮助诊断
            actual = next((l.strip() for l in out.splitlines() if "→ 类型识别" in l), "?")
            print(f"[FAIL] {os.path.basename(path)}  期望 {expect}  | 实际 {actual}")
            failed += 1
    print("=" * 60)
    print(f"通过 {passed} | 失败 {failed} | 跳过 {skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
