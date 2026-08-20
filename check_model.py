"""
模型结构验证脚本 — 通用量化格式检测（不绑定任何特定插件）

用法:
    F:/ComfyUI-aki-v3/python/python.exe check_model.py <模型路径> [更多路径...]
    F:/ComfyUI-aki-v3/python/python.exe check_model.py <目录>     # 批量扫描目录下所有 .safetensors

输出说明:
    1. 文件大小 + fp16 参考大小 → 判断压缩比
    2. key 后缀统计 → 看有哪些量化伴生张量
    3. 权重 dtype → 判断是 int8 / int4 打包 / 未量化
    4. scale 维度 → 判断是 tensorwise / per-row / group-wise
    5. 综合判定 → 格式类型 + 关键结构参数（只对模型下结论）

设计原则:
    - 通用性: 只检测模型本身, 不输出任何特定插件(如 wa4 loader)的可用性判断
    - 证据链闭合: 结论必须由「主导格式」(采样层中出现最多的结构) + 决定性验证推导
    - 插件即探针: 特定插件的实现细节可用于反向发现通用检测逻辑的漏洞(如伪 W4A4 死分支),
      但修复后的判定逻辑保持插件无关
    - 文件名/元数据/直方图都不可信, 只有解包验证是硬证据

判定优先级（从高到低）:
    1. bitsandbytes__nf4 标记    → NF4 (bnb 量化, absmax+quant_map 码本)
    2. comfy_quant 元数据        → ComfyUI 官方量化格式 (按 format 细分)
    3. weight_zp + int32 打包    → 非对称 INT4
    4. 2D scale + 解包验证通过   → Packed INT4 (group-wise, nunchaku 风格)
    5. 1D scale + 解包验证通过   → Packed INT4 (per-row, 无 group_size)
    6. 全宽 int8 + gs 虚标       → 伪 W4A4 / Group-wise INT8 (命名虚标)
    7. 纯 int8 无打包            → INT8 / INT8+BF16 混合
    8. 压缩比 >=75% 无量化结构   → 接近未量化 (FP16/BF16/FP8)
    9. 兜底                      → 格式不明确
"""
import sys, os, json, struct
import warnings

# 吞掉环境噪声 (如 torch.cuda 导入时的 pynvml FutureWarning), 保持控制台极简
# 必须在 import torch 之前生效, 否则导入期警告仍会打印
warnings.filterwarnings("ignore", category=FutureWarning)

import torch
import safetensors.torch as st
from collections import Counter

__version__ = "1.1.0"

BANNER = "=" * 80
SECTION = "-" * 80


# ==================== 本地化 (i18n) ====================
def _detect_lang():
    """--lang auto|zh|en; 默认 auto 按系统 locale 选择"""
    for i, a in enumerate(sys.argv):
        if a == "--lang" and i + 1 < len(sys.argv):
            v = sys.argv[i + 1].lower()
            if v in ("zh", "en"):
                return v
    loc = ""
    try:
        import locale
        loc = (locale.getlocale() or ("", ""))[0] or ""
        if not loc:
            enc = (locale.getpreferredencoding(False) or "").lower()
            return "zh" if enc in ("cp936", "gbk", "gb2312", "gb18030") else "en"
    except Exception:
        loc = ""
    if not loc:
        loc = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "")
    loc_l = loc.lower()
    # Windows locale 名如 "Chinese (Simplified)_China" 不以 zh 开头
    return "zh" if (loc_l.startswith("zh") or "chinese" in loc_l or "prc" in loc_l) else "en"


LANG = _detect_lang()

# 文案字典: key → (中文, English)
_T = {
    # 板块标题
    "sec_quick": ("💡 【普通用户速览】", "💡 Quick Overview"),
    "sec_perf": ("📊 【性能与结构评估】", "📊 Performance & Structure"),
    "sec_diag": ("🔍 【开发者深度诊断】", "🔍 Deep Diagnosis"),
    "sec_audit": ("📋 【文件名审计】", "📋 Filename Audit"),
    "sec_final": ("【终审结论】", "Final Verdict"),
    # 子块标题
    "sub_activation": ("[激活位宽 (A) 推导]", "[Activation Width (A) Derivation]"),
    "sub_tensor": ("[张量与数据类型分布]", "[Tensor & dtype Distribution]"),
    "sub_ratio": ("[压缩比评估 (双解读分析)]", "[Compression Ratio (dual interpretation)]"),
    "sub_sample": ("[量化层与采样验证]", "[Quantized Layers & Sample Verification]"),
    "sub_orig": ("[原始文件名]", "[Original Filename]"),
    "sub_check": ("[逐段核验]", "[Per-Segment Verification]"),
    "sub_stdname": ("[标准化命名]", "[Standardized Naming]"),
    # 字段标签
    "f_type": ("模型类型", "Type"),
    "f_load": ("典型加载", "Loader"),
    "f_vram": ("显存参考", "VRAM ref"),
    "f_raw": ("原始等效", "Raw equiv"),
    "f_save": ("显存节省", "VRAM saving"),
    "f_proto": ("量化协议", "Quant protocol"),
    "f_algo": ("关键算法", "Key algorithm"),
    "f_mech": ("量化机制", "Quant mechanism"),
    "f_meta": ("模型元数据", "Model metadata"),
    "f_suffix": ("文件后缀", "File suffix"),
    "f_w": ("权重位宽 W", "Weight width W"),
    "f_a": ("激活位宽 A", "Activation width A"),
    "f_arch": ("架构", "Arch"),
    "f_params": ("参数量", "Params"),
    "f_quant": ("量化/精度", "Quant/Precision"),
    # 状态词
    "s_claimed": ("声称", "claimed"),
    "s_file": ("文件", "file"),
    "s_ok": ("✓ 一致", "✓ match"),
    "s_bad": ("✗ 声称不符", "✗ mismatch"),
    "s_bad2": ("✗ 不符", "✗ mismatch"),
    "s_undecl": ("文件名未声明", "not declared in name"),
    "s_fill": ("补全", "filled"),
    "s_unknown": ("未知", "unknown"),
    "s_unverif": ("未校验", "unverified"),
    "s_infer": ("推断", "inferred"),
    "s_engine": ("⚠ 引擎相关, 非文件证据", "⚠ engine-dependent, not file evidence"),
    "s_base_fp16": ("(FP16 基础)", "(FP16 base)"),
    "s_est": ("估算", "est."),
    "s_disk": ("磁盘", "disk"),
    "s_keep_ident": ("(身份段保留原样, 仅量化段按文件证据更正/补全)",
                     "(identity segments kept as-is; only quant segment corrected/filled by file evidence)"),
    # 终审判据行
    "v_weight_scale": ("含 weight_scale 伴生张量", "weight_scale companion tensors"),
    "v_comfy_quant": ("含 comfy_quant 元数据", "comfy_quant metadata"),
    "v_nf4": ("含 bitsandbytes__nf4 标记", "bitsandbytes__nf4 markers"),
    "v_bnb": ("含 bnb 量化伴生", "bnb quant companions"),
    "v_zp": ("含 weight_zp (zero-point)", "weight_zp (zero-point)"),
    "v_int8": ("存在 int8 张量", "int8 tensors present"),
    "v_int32": ("存在 int32 打包权重", "int32 packed weights"),
    "v_bf16": ("存在 bf16 (未量化层)", "bf16 (unquantized) present"),
    "v_ratio": ("压缩比", "ratio"),
    "v_struct": ("结构", "structure"),
    "v_meta": ("元数据", "metadata"),
    "v_true4": ("真 INT4 级", "true INT4 level"),
    "v_dual": ("双解读差异大", "large dual-interpretation gap"),
    # 终审行
    "f_type_id": ("类型识别", "Type"),
    "f_basis": ("识别依据", "Basis"),
    "f_load_advice": ("典型加载", "Loader"),
    "f_struct_pt": ("结构要点", "Structure"),
    # 其他
    "batch_summary": ("【批量扫描汇总】", "Batch Scan Summary"),
    "no_files": ("未找到 .safetensors / .gguf 文件", "No .safetensors / .gguf files found"),
    "unk_arch": ("未知（文件名/文件均无架构线索）", "unknown (no arch clue in name or file)"),
    # 量化机制 (m_*)
    "m_nf4": ("归一化 4bit：16 值非线性码本 + 块级 absmax 缩放",
              "Normalized 4-bit: 16-value non-linear codebook + block-wise absmax scaling"),
    "m_int8_convrot": ("QuaRot 旋转消除离群值 → 通道级 int8 定点缩放",
                       "QuaRot rotation removes outliers → channel-wise int8 fixed-point scaling"),
    "m_int8": ("通道级/tensorwise int8 定点缩放", "Channel/tensor-wise int8 fixed-point scaling"),
    "m_tint4": ("int32 打包（每 int32 装 8×4bit）+ 通道级缩放",
                "int32 packing (8×4bit per int32) + channel-wise scaling"),
    "m_fp8": ("8-bit 浮点（e4m3/e5m2 指数尾数分配）",
              "8-bit float (e4m3/e5m2 exponent-mantissa split)"),
    "m_packed_gw": ("字节打包：每字节 2×4bit，group-wise 缩放",
                    "Byte packing: 2×4bit per byte, group-wise scaling"),
    "m_packed_row": ("字节打包：每字节 2×4bit，per-row 缩放",
                     "Byte packing: 2×4bit per byte, per-row scaling"),
    "m_torchao": ("affine int32 打包（每 int32 装 8×4bit）",
                  "Affine int32 packing (8×4bit per int32)"),
    "m_asym": ("zero-point 非对称量化（int32 打包）",
               "Zero-point asymmetric quantization (int32 packing)"),
    "m_gw_int8": ("group-wise int8 定点（组共享 scale）",
                  "Group-wise int8 fixed-point (group-shared scale)"),
    "m_unquant": ("无压缩（原生 fp16/bf16 精度）", "Uncompressed (native fp16/bf16)"),
    "m_mixed": ("部分层量化（量化层 + 原生精度层混合）",
                "Partial quantization (quantized + native layers mixed)"),
}


def _l(key):
    """取当前语言的文案"""
    return _T[key][1] if LANG == "en" else _T[key][0]


COMMON_GROUP_SIZES = {16, 32, 64, 128, 256, 512}


def print_file_head(fsize, path):
    """文件头部横幅 + 基本信息"""
    print()
    print(BANNER)
    if LANG == "en":
        print(f" 📦 MFV Model Checker v{__version__}")
    else:
        print(f" 📦 MFV 模型检查工具 v{__version__}")
    print(BANNER)
    if LANG == "en":
        print(f" File: {os.path.basename(path)}")
        print(f" Size: {fsize:.2f} GB")
        print(f" Path: {os.path.dirname(path) or os.getcwd()}")
    else:
        print(f" 文件: {os.path.basename(path)}")
        print(f" 大小: {fsize:.2f} GB")
        print(f" 路径: {os.path.dirname(path) or os.getcwd()}")


def _type_en(type_str):
    """类型识别文案 → 英文 (术语保留; 纯中文描述翻译)"""
    t = type_str
    pairs = (
        ("接近未量化 (fp16 为主)", "near-unquantized (fp16 dominant)"),
        ("接近未量化 (bf16 为主)", "near-unquantized (bf16 dominant)"),
        ("接近未量化", "near-unquantized"),
        ("混合精度", "mixed precision"),
        ("非对称 INT4", "asymmetric INT4"),
        ("伪 W4A4 (Group-wise INT8)", "fake W4A4 (Group-wise INT8)"),
        ("torchao int32 打包", "torchao int32 packed"),
        ("分块量化", "block-quantized"),
        ("量化", "quantized"),
        ("主导", "dominant"),
        ("打包 (无 group_size)", "packed (no group_size)"),
        ("打包", "packed"),
        ("无 group_size", "no group_size"),
        ("风格", "-style"),
    )
    for zh, en in pairs:
        t = t.replace(zh, en)
    return t


def _load_en(load):
    """加载建议文案 → 英文"""
    t = load
    pairs = (
        ("ComfyUI 原生加载 (QUANT_ALGOS 内置, 无需第三方节点)",
         "ComfyUI native loader (built-in QUANT_ALGOS, no third-party nodes)"),
        ("ComfyUI 原生 / TINT4 节点 (comfy_quant 协议)",
         "ComfyUI native / TINT4 nodes (comfy_quant protocol)"),
        ("bitsandbytes NF4 加载器 (bnb 原生, 非 ComfyUI 协议)",
         "bitsandbytes NF4 loader (bnb native, not ComfyUI protocol)"),
        ("llama.cpp 系 (ollama / llama-server / GGUF 原生)",
         "llama.cpp family (ollama / llama-server / GGUF native)"),
        ("标准 fp16/bf16 加载器", "standard fp16/bf16 loader"),
        ("支持字节打包 INT4 的后端 (如 nunchaku 系)",
         "backend supporting byte-packed INT4 (e.g. nunchaku)"),
        ("支持 per-row INT4 打包的后端 (非标准 nunchaku 布局)",
         "backend supporting per-row INT4 packing (non-standard nunchaku layout)"),
        ("原生精度加载 (fp16/bf16)", "native precision loader (fp16/bf16)"),
    )
    for zh, en in pairs:
        t = t.replace(zh, en)
    return t


def print_sec_quick(type_str, load_advice, fsize, ratio=None):
    """💡 普通用户速览区"""
    print()
    print(SECTION)
    print(" " + _l("sec_quick"))
    print(SECTION)
    print(f"  • {_l('f_type')}: {_type_en(type_str) if LANG == 'en' else type_str}")
    if load_advice:
        print(f"  • {_l('f_load')}: {_load_en(load_advice) if LANG == 'en' else load_advice}")
    if ratio is not None:
        if LANG == "en":
            print(f"  • {_l('f_vram')}: disk {fsize:.2f} GB (est.; ~{100 - ratio:.0f}% saved vs full FP16)")
        else:
            print(f"  • {_l('f_vram')}: 磁盘 {fsize:.2f} GB (估算; 相比全 FP16 约省 {100 - ratio:.0f}%)")
    else:
        if LANG == "en":
            print(f"  • {_l('f_vram')}: disk {fsize:.2f} GB (est., runtime VRAM depends on engine)")
        else:
            print(f"  • {_l('f_vram')}: 磁盘 {fsize:.2f} GB (估算, 推理占用视引擎而定)")


def print_sec_perf(fp16_gb, final_ratio, protocol, algo, save_note, mechanism=""):
    """📊 性能与结构评估区 (介于速览与深度诊断之间)"""
    print()
    print(SECTION)
    print(" " + _l("sec_perf"))
    print(SECTION)
    print(f"  • {_l('f_raw')}: ~{fp16_gb:.2f} GB {_l('s_base_fp16')}")
    if final_ratio is not None:
        save_pct = 100 - final_ratio
        if save_pct > 0.5:  # 阈值防浮点噪声 (-0% / 0.01%)
            print(f"  • {_l('f_save')}: ~{save_pct:.0f}% ⏬ ({save_note})")
        else:
            print(f"  • {_l('f_save')}: ~0% ({save_note})")
    print(f"  • {_l('f_proto')}: {protocol}")
    if algo:
        print(f"  • {_l('f_algo')}: {algo}")
    if mechanism:
        print(f"  • {_l('f_mech')}: {mechanism}")


def _quant_mechanism(type_str, comfy_quant_info=None):
    """把检测到的结构翻译为量化机制一句话（原理注记, 对应量化方法速查表的「思路」列）"""
    tl = type_str.lower()
    fl = ((comfy_quant_info or {}).get("format", "") or "").lower()
    if "nf4" in tl and "bitsandbytes" in tl:
        return _l("m_nf4")
    if "comfyui int8" in tl or "int8" in fl:
        if comfy_quant_info and comfy_quant_info.get("convrot"):
            return _l("m_int8_convrot")
        return _l("m_int8")
    if "comfyui int4" in tl or "tint4" in fl:
        return _l("m_tint4")
    if ("fp8" in fl or "float8" in fl):
        return _l("m_fp8")
    if "packed int4" in tl:
        return _l("m_packed_gw") if "group-wise" in tl else _l("m_packed_row")
    if "torchao" in tl:
        return _l("m_torchao")
    if "非对称" in tl:
        return _l("m_asym")
    if "group-wise int8" in tl:
        return _l("m_gw_int8")
    if "接近未量化" in tl:
        return _l("m_unquant")
    if "混合精度" in tl:
        return _l("m_mixed")
    return ""


def _print_activation_block(meta, suffix, w, a):
    """📊 区内的「激活位宽 (A) 推导」块: 证据链(元数据→后缀→W→A)"""
    print()
    print(" " + _l("sub_activation"))
    print(f"  • {_l('f_meta')}: {meta}")
    print(f"  • {_l('f_suffix')}:   {suffix}")
    print(f"  • {_l('f_w')}: {w}")
    print(f"  • {_l('f_a')}: {a}   {_l('s_engine')}")


def _activation_derive_safetensors(sd, type_str, comfy_quant_info):
    """推导 safetensors 激活位宽参考要素, 返回 (meta, w, a)。

    W 从文件结构推导(非文件名); A 是 W×引擎族的候选参考(非文件证据)。
    """
    tl = type_str.lower()
    en = LANG == "en"
    # --- 模型元数据 ---
    if comfy_quant_info:
        parts = [f"comfy_quant.format={comfy_quant_info.get('format', '?')}"]
        if comfy_quant_info.get("per_block"):
            parts.append("per_block")
        if comfy_quant_info.get("quarot"):
            parts.append("quarot")
        if comfy_quant_info.get("group_size"):
            parts.append(f"gs={comfy_quant_info['group_size']}")
        meta = ", ".join(parts)
    elif "bitsandbytes" in tl:
        n = sum(1 for k in sd if k.endswith(".bitsandbytes__nf4"))
        meta = f"bitsandbytes__nf4 ({n} " + ("groups)" if en else "组)") 
    else:
        meta = "no proprietary quant metadata" if en else "无专有量化元数据"
    # --- 权重位宽 W (从文件结构推导) ---
    if "int4" in tl or "packed int4" in tl or "torchao" in tl or "非对称" in tl:
        w = "4 bit"
    elif "nf4" in tl:
        w = "4 bit (uint8 packed 2×4bit)" if en else "4 bit（uint8 打包 2×4bit）"
    elif ("fp8" in tl or "float8" in tl):
        w = "8 bit (FP8 float)" if en else "8 bit（FP8 浮点）"
    elif "int8" in tl or "group-wise" in tl:
        w = "8 bit (int8 fixed-point)" if en else "8 bit（int8 定点）"
    elif "接近未量化" in tl:
        w = "16 bit (native fp16/bf16)" if en else "16 bit（原生 fp16/bf16）"
    elif "混合精度" in tl:
        w = "mixed (quantized + native)" if en else "混合（量化层 + 原生层）"
    else:
        w = "pending" if en else "待定"
    # --- 激活位宽 A (W×引擎族候选, 非文件证据) ---
    has_svdq = any("svdq" in k.lower() or "lora_" in k.lower() for k in sd)
    if has_svdq and "int4" in tl:
        a = "A4 (design-locked, full 4-bit)" if en else "A4（设计锁定, 全 4bit 推理）"
    elif ("fp8" in tl or "float8" in tl):
        a = "A8-FP8 primary (HW), A16 optional" if en else "A8-FP8 为主（硬件配套）, A16 可选"
    elif "int8" in tl or "group-wise" in tl:
        a = "A8 primary, A16 optional" if en else "A8 为主, A16 可选"
    elif "nf4" in tl:
        a = "A16 primary, A8 optional" if en else "A16 为主, A8 可选"
    elif "int4" in tl or "torchao" in tl or "非对称" in tl:
        a = ("A16 primary (ComfyUI default), A8 optional" if en else "A16 为主（ComfyUI 默认）, A8 可选") \
            if comfy_quant_info else ("A16 primary, A8 optional" if en else "A16 为主, A8 可选")
    elif "接近未量化" in tl:
        a = "A16 (no activation quant)" if en else "A16（无激活量化）"
    elif "混合精度" in tl:
        a = "depends on quantized layers" if en else "视量化层而定"
    else:
        a = "pending" if en else "待定"
    return meta, w, a


# ==================== 文件名审计 ====================
# 文件名量化声称词表 (剥离/核验用)
_QUANT_TOKENS = ("fp16", "fp32", "bf16", "f16", "f32", "fp8", "float8",
                 "e4m3", "e5m2", "int8", "int4", "uint8", "nf4", "fp4",
                 "q2_k", "q3_k", "q4_0", "q4_1", "q4_k", "q5_0", "q5_1",
                 "q5_k", "q6_k", "q8_0", "q8_k", "iq2", "iq3", "iq4",
                 "tint4", "convrot", "awq", "gptq", "mixed", "torchao",
                 "8bit", "4bit", "16bit")

# 文件名架构词 → 家族 (safetensors 无架构元数据, 家族级推断)
_ARCH_TOKENS = (("qwen", "Qwen"), ("mistral", "Mistral"), ("llama", "LLaMA"),
                ("flux", "Flux"), ("sdxl", "SDXL"), ("umt5", "UMT5"),
                ("minimax", "MiniMax"), ("t5", "T5"), ("clip", "CLIP"),
                ("vit", "ViT"), ("gemma", "Gemma"), ("deepseek", "DeepSeek"),
                ("grok", "Grok"), ("phi", "Phi"), ("moonshot", "Moonshot"),
                ("kimi", "Moonshot"), ("glm", "GLM"), ("internlm", "InternLM"),
                ("llava", "LLaVA"), ("sd", "SD"))


def _strip_quant_from_name(stem):
    """从文件名主干剥离量化声称段, 返回 (身份段, 量化声称段或None)。

    分段逻辑: 先按 '-' 分; 段内先查子段(_ 连接)量化词, 子段拆不出
    但整段含量化词(如 Q3_K_S)则整段视为量化段。身份段 = 剥离后重组。
    """
    parts = stem.split("-")
    out_parts, quant_parts = [], []
    for p in parts:
        subs = p.split("_")
        matched = [s for s in subs if any(t in s.lower() for t in _QUANT_TOKENS)]
        if matched:
            keep = [s for s in subs if s not in matched]
            if keep:
                out_parts.append("_".join(keep))
            quant_parts.append("_".join(matched))
        elif any(t in p.lower() for t in _QUANT_TOKENS):
            # 子段拆不出但整段是量化类型 (如 Q3_K_S)
            quant_parts.append(p)
        else:
            out_parts.append(p)
    ident = "-".join(out_parts).strip("-")
    return ident, ("-".join(quant_parts) if quant_parts else None)


def _standard_quant_tag(type_str, comfy_quant_info=None, dominant_name=None):
    """type_str → 文件名友好的标准量化段 (供标准化命名替换/补全)"""
    tl = type_str.lower()
    if "gguf" in tl:
        return dominant_name or "GGUF"
    if "comfyui fp8" in tl or "float8" in tl or ("fp8" in tl and "comfyui" in tl):
        fmt = (comfy_quant_info or {}).get("format", "")
        return ("FP8-" + fmt.replace("float8_", "")) if "float8" in fmt else "FP8"
    if "comfyui int4" in tl or "tint4" in tl:
        return "ComfyUI-INT4"
    if "comfyui int8" in tl or ("int8" in tl and "comfyui" in tl):
        return "ComfyUI-INT8"
    if "nf4" in tl:
        return "NF4"
    if "int4" in tl:
        if "per-row" in tl:
            return "INT4-per-row"
        if "tensorwise" in tl:
            return "INT4-tensorwise"
        return "INT4-groupwise" if "group-wise" in tl else "INT4"
    if "torchao" in tl:
        return "INT4-torchao"
    if "接近未量化" in tl:
        return "fp16"
    if "混合精度" in tl:
        return "mixed"
    return type_str


def _arch_from_name(fname):
    """从文件名提取架构家族词"""
    stem = os.path.splitext(os.path.basename(fname))[0].lower()
    for token, family in _ARCH_TOKENS:
        if token in stem:
            return family
    return None


def _arch_from_keys(sd):
    """从 safetensors key 推断架构家族 (家族级)"""
    keys = " ".join(sd.keys()).lower()
    if "double_blocks" in keys or "single_blocks" in keys:
        return "Flux"
    if "diffusion_transformer" in keys and "transformer" in keys and "layers" in keys:
        return "MiniMax"
    if "blocks." in keys and "attn" in keys and "mlp" in keys:
        return "MiniMax"
    if "model.embed_tokens" in keys or "model.layers" in keys:
        return "Qwen/Mistral 系"
    if ("text_model" in keys and "transformer" in keys) or "eos_token_embeddings" in keys:
        return "CLIP/T5 系"
    return None


def _print_audit(filename, ident, quant_claimed, std_quant, arch_s, params_s, suggest):
    """📋 文件名审计板块: 原始文件名 → 逐段核验 → 标准化命名"""
    print()
    print(SECTION)
    print(" " + _l("sec_audit"))
    print(SECTION)
    print(" " + _l("sub_orig"))
    print(f"  {filename}")
    print()
    print(" " + _l("sub_check"))
    print(f"  • {_l('f_arch')}:   {arch_s}")
    print(f"  • {_l('f_params')}: {params_s}")
    if quant_claimed:
        claimed_l = quant_claimed.lower()
        ok = any(t in claimed_l for t in (std_quant.lower().replace("-", ""),)) or \
             any(t in claimed_l for t in _quant_synonyms(std_quant))
        mark = _l("s_ok") if ok else _l("s_bad")
        print(f"  • {_l('f_quant')}: {_l('s_claimed')} {quant_claimed}  →  {_l('s_file')} {std_quant}  {mark}")
    else:
        if LANG == "en":
            print(f"  • {_l('f_quant')}: {_l('s_undecl')}  →  file {std_quant} ({_l('s_fill')})")
        else:
            print(f"  • {_l('f_quant')}: {_l('s_undecl')}  →  文件 {std_quant}（{_l('s_fill')}）")
    print()
    print(" " + _l("sub_stdname"))
    print(f"  {suggest}")
    print("  " + _l("s_keep_ident"))


def _quant_synonyms(std_quant):
    """标准量化段 → 可接受的声称同义词 (宽松匹配)"""
    s = std_quant.lower().replace("-", "")
    m = {
        "comfyuiint4": ("int4", "4bit", "tint4"),
        "comfyuiint8": ("int8", "8bit"),
        "int4perrow": ("int4", "4bit"),
        "int4groupwise": ("int4", "4bit"),
        "nf4": ("nf4", "4bit"),
        "int4torchao": ("int4", "4bit", "torchao"),
    }
    return m.get(s, (s,))


def _arch_family(name):
    """架构名归一化到家族 (处理别名: t5encoder/umt5→T5, qwen3/qwen→Qwen 等)"""
    n = (name or "").lower()
    for alias, fam in (("t5encoder", "T5"), ("umt5", "T5"), ("t5", "T5"),
                       ("qwen", "Qwen"), ("mistral", "Mistral"), ("llama", "LLaMA"),
                       ("flux", "Flux"), ("clip", "CLIP"), ("vit", "ViT"),
                       ("minimax", "MiniMax"), ("grok", "Grok"), ("phi", "Phi"),
                       ("moonshot", "Moonshot"), ("glm", "GLM"),
                       ("internlm", "InternLM"), ("llava", "LLaVA")):
        if alias in n:
            return fam
    return name or ""


def _run_audit(fname, ident, quant_claimed, std_quant, arch_claimed, arch_file, total_elems):
    """执行文件名审计: 架构/参数量/量化逐段核验 + 生成标准化命名, 渲染📋板块"""
    import re
    en = LANG == "en"
    arrow = " → " if not en else " -> "
    # --- 架构核验 ---
    if arch_file and arch_claimed:
        cf, ff = _arch_family(arch_claimed), _arch_family(arch_file)
        ok = cf and ff and (cf == ff or cf in ff or ff in cf)
        if ok:
            arch_s = f"{_l('s_claimed')} {arch_claimed}{arrow}{_l('s_file')} {arch_file}  {_l('s_ok')}"
        else:
            arch_s = f"{_l('s_claimed')} {arch_claimed}{arrow}{_l('s_file')} {arch_file}  {_l('s_bad2')}"
    elif arch_file:
        if en:
            arch_s = f"file {arch_file} (arch not declared in name)"
        else:
            arch_s = f"文件 {arch_file}（文件名未声明架构）"
    elif arch_claimed:
        if en:
            arch_s = f"{_l('s_claimed')} {arch_claimed} (file cannot infer, {_l('s_unverif')})"
        else:
            arch_s = f"{_l('s_claimed')} {arch_claimed}（文件无法推断, {_l('s_unverif')}）"
    else:
        arch_s = _l("unk_arch")
    # --- 参数量核验 ---
    m = re.search(r'(\d+(?:[._]\d+)?)\s*[bB](?![a-z])', fname)
    actual_b = total_elems / 1e9 if total_elems else 0
    if m:
        claimed_b = float(m.group(1).replace("_", "."))
        ok = actual_b > 0 and abs(claimed_b - actual_b) / actual_b < 0.15
        if ok:
            params_s = f"{_l('s_claimed')} {claimed_b:.1f}B{arrow}{_l('s_file')} {actual_b:.1f}B  {_l('s_ok')}"
        elif actual_b > 0 and claimed_b > actual_b * 1.3:
            if en:
                params_s = (f"{_l('s_claimed')} {claimed_b:.1f}B{arrow}{_l('s_file')} {actual_b:.1f}B  {_l('s_bad2')}"
                            f" (⚠ maybe single-tower/pruned: name claims full-model params, file holds text tower only)")
            else:
                params_s = (f"{_l('s_claimed')} {claimed_b:.1f}B{arrow}{_l('s_file')} {actual_b:.1f}B  {_l('s_bad2')}"
                            f"（⚠ 可能为单塔/裁剪: 文件名标全模型参数, 文件仅含文本塔）")
        else:
            params_s = f"{_l('s_claimed')} {claimed_b:.1f}B{arrow}{_l('s_file')} {actual_b:.1f}B  {_l('s_bad2')}"
    else:
        if en:
            params_s = f"{_l('s_undecl')}{arrow}file {actual_b:.1f}B ({_l('s_fill')})"
        else:
            params_s = f"{_l('s_undecl')}{arrow}文件 {actual_b:.1f}B（{_l('s_fill')}）"
    # --- 标准化命名: 身份段保留 + 参数量缺失补 + 量化段替换/补全 ---
    parts = [ident] if ident else []
    if not m and ident and actual_b > 0:
        parts.append(f"{actual_b:.1f}B")
    if std_quant:
        parts.append(std_quant)
    suggest = "-".join(parts) if parts else ("(cannot suggest)" if en else "（无法生成建议）")
    _print_audit(fname, ident, quant_claimed, std_quant, arch_s, params_s, suggest)


def print_sec_diag():
    """🔍 开发者深度诊断区标题"""
    print()
    print(SECTION)
    print(" " + _l("sec_diag"))
    print(SECTION)


def _verdict_en(text):
    """终审 verdict 证据文案 → 英文 (常用短语替换; 术语/数字保留)"""
    t = text
    pairs = (
        ("含 weight_scale 伴生张量", "weight_scale companion tensors"),
        ("含 comfy_quant 元数据", "comfy_quant metadata"),
        ("含 weight_zp (zero-point)", "weight_zp (zero-point)"),
        ("含 w4a4_group_size", "w4a4_group_size"),
        ("含 bnb 量化伴生 (absmax+quant_map)", "bnb quant companions (absmax+quant_map)"),
        ("含 bitsandbytes__nf4 标记", "bitsandbytes__nf4 markers"),
        ("存在 int8 张量", "int8 tensors present"),
        ("存在 int32 打包权重 (疑似 torchao)", "int32 packed weights (likely torchao)"),
        ("存在 bf16 (未量化层)", "bf16 (unquantized) present"),
        ("无偏", "unbiased"),
        ("打包", "packed"),
        ("双解读差异大", "large dual-interpretation gap"),
        ("真 INT4 级", "true INT4 level"),
        ("INT8 级", "INT8 level"),
        ("混合精度", "mixed precision"),
        ("接近未量化", "near-unquantized"),
        ("层主导", "layers dominant"),
        ("层主导, gs 虚标", "layers dominant, gs mislabeled"),
        ("无 weight_scale", "no weight_scale"),
        ("可能未量化 或 GGUF 另行处理", "possibly unquantized or handled as GGUF"),
        ("quarot 旋转", "quarot rotation"),
        ("待定", "pending"),
        ("需结合权重宽度判断 (可能真 INT4 或伪 W4A4)",
         "needs weight-width check (true INT4 or fake W4A4)"),
        ("个 → ComfyUI 量化协议", "-> ComfyUI protocol"),
        ("int32 weight + scale", "int32 weight + scale"),
        ("可能未量化", "possibly unquantized"),
        ("量化层 + 原生精度层混合", "quantized + native layers"),
        ("quant_state 标记", "quant_state markers"),
        ("absmax/quant_map 码本", "absmax/quant_map codebook"),
        ("决定性物理证据", "decisive physical evidence"),
        ("量化层", "quantized layers"),
        ("未量化", "unquantized"),
        ("部分量化", "partially quantized"),
        ("采样结构", "sampled structure"),
        ("comfy_quant 专有元数据 (ComfyUI 官方量化协议, 非 torchao)",
         "comfy_quant proprietary metadata (ComfyUI official protocol, not torchao)"),
        ("无量化伴生张量", "no quant companion tensors"),
        ("int32 打包", "int32 packing"),
        ("解包验证通过", "unpack verification passed"),
        ("解包验证", "unpack verification"),
        ("nibble 缺失", "nibble absence"),
        ("合法 4bit", "valid 4-bit"),
        ("字节 unique 组合", "byte unique combination"),
        ("格式分布", "format distribution"),
        ("缩放", "scaling"),
        ("旋转消除离群值", "rotation removes outliers"),
        ("通道级", "channel-wise"),
        ("主导格式", "dominant format"),
        ("主导", "dominant"),
        ("候选参考", "candidate reference"),
        ("块级", "block-wise"),
        ("码本", "codebook"),
        ("标记", "markers"),
        ("伴生张量", "companion tensors"),
        ("张量", "tensors"),
        ("层", "layers"),
        ("旋转", "rotation"),
    )
    for zh, en in pairs:
        t = t.replace(zh, en)
    return t


def print_sec_final():
    """终审结论区标题"""
    print()
    print(BANNER)
    print(" " + _l("sec_final"))
    print(BANNER)

# GGML 张量类型枚举 (llama.cpp ggml.h) → 名称
GGML_TYPE_NAMES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 4: "Q4_2", 5: "Q4_3",
    6: "Q5_0", 7: "Q5_1", 8: "Q8_0", 9: "Q8_1", 10: "Q2_K", 11: "Q3_K",
    12: "Q4_K", 13: "Q5_K", 14: "Q6_K", 15: "Q8_K", 16: "IQ2_XXS", 17: "IQ2_XS",
    18: "IQ3_XXS", 19: "IQ1_S", 20: "IQ4_NL", 21: "IQ3_S", 22: "IQ2_S",
    23: "IQ4_XS", 24: "I8", 25: "I16", 26: "I32", 27: "I64", 28: "F64",
    29: "IQ1_M", 30: "BF16",
}
# 每元素近似位宽 (分块量化含块内 scale 开销, 校准自 llama.cpp block_size/type_size; IQ 系为标称值)
GGML_TYPE_BITS = {
    0: 32, 1: 16, 2: 4.5, 3: 5.5, 4: 4.5, 5: 5.5, 6: 5.5, 7: 6.5,
    8: 8.5, 9: 9.5, 10: 4.1, 11: 4.0, 12: 4.5, 13: 6.0, 14: 6.5, 15: 8.5,
    16: 3.0, 17: 3.0, 18: 3.25, 19: 1.75, 20: 4.25, 21: 3.5, 22: 2.75,
    23: 4.5, 24: 8, 25: 16, 26: 32, 27: 64, 28: 64, 29: 1.9, 30: 16,
}


def _gguf_read_string(f):
    """GGUF 字符串: uint64 长度 + UTF-8 字节"""
    n = struct.unpack("<Q", f.read(8))[0]
    return f.read(n).decode("utf-8", errors="replace")


def _gguf_read_value(f, vtype):
    """GGUF KV 值读取 (类型 0-12), ARRAY 递归"""
    if vtype == 0:   return struct.unpack("<B", f.read(1))[0]
    if vtype == 1:   return struct.unpack("<b", f.read(1))[0]
    if vtype == 2:   return struct.unpack("<H", f.read(2))[0]
    if vtype == 3:   return struct.unpack("<h", f.read(2))[0]
    if vtype == 4:   return struct.unpack("<I", f.read(4))[0]
    if vtype == 5:   return struct.unpack("<i", f.read(4))[0]
    if vtype == 6:   return struct.unpack("<f", f.read(4))[0]
    if vtype == 7:   return bool(struct.unpack("<B", f.read(1))[0])
    if vtype == 8:   return _gguf_read_string(f)
    if vtype == 9:   # ARRAY
        elem_type = struct.unpack("<I", f.read(4))[0]
        cnt = struct.unpack("<Q", f.read(8))[0]
        return [_gguf_read_value(f, elem_type) for _ in range(cnt)]
    if vtype == 10:  return struct.unpack("<Q", f.read(8))[0]
    if vtype == 11:  return struct.unpack("<q", f.read(8))[0]
    if vtype == 12:  return struct.unpack("<d", f.read(8))[0]
    raise ValueError(f"未知 GGUF KV 类型 {vtype}")


def verify_unpack4(w, sample=1_000_000):
    """解码验证: 判断 int8/uint8 权重是否为 4bit 字节打包。

    关键: 任何字节拆成两个 nibble 都天然 ∈[-8,7] 且 ≤16 值 — 单看解包值域无区分力!
    必须检查「字节 unique 数量」和「nibble 缺失模式」:
    - 全宽 int8 量化权重: 字节 unique 接近 256 (如 255), lo/hi nibble 全覆盖 16
    - 真 4bit 打包: 每字节存两个 4bit 量化值, nibble 值域受限,
      字节 unique = lo_unique × hi_unique (如 15×15=225), 出现 nibble 缺失
    返回 (is_packed, byte_unique, lo_unique, hi_unique)
    """
    if not isinstance(w, torch.Tensor) or w.dtype not in (torch.int8, torch.uint8):
        return None, 0, 0, 0
    flat = w.flatten()[:sample].to(torch.int16)
    byte_u = torch.unique(flat).numel()
    lo_u = torch.unique(flat & 0x0F).numel()
    hi_u = torch.unique((flat >> 4) & 0x0F).numel()
    # 打包特征: nibble 有缺失 (unique < 16) 且字节组合约等于 lo×hi
    nibble_missing = (lo_u < 16) or (hi_u < 16)
    combo_exact = byte_u <= lo_u * hi_u and byte_u >= lo_u * hi_u * 0.8
    if nibble_missing and combo_exact:
        return True, byte_u, lo_u, hi_u
    return False, byte_u, lo_u, hi_u


def infer_group_size(weight_w, scale, gs_meta=None, ratio=None, numeric_packed=None,
                     unpack_ok=None):
    """推断 group-wise 量化布局: 返回 (verdict, detail)。

    - weight_w: weight 第二维 (K)
    - scale: 2D scale 张量
    - gs_meta: w4a4_group_size 元数据值 (可能虚标)
    - ratio: 无偏压缩比 % (参考, 不参与打包裁决)
    - numeric_packed: 数值检测 (仅供参考, 不再作决定性判据)
    - unpack_ok: 4bit 解包验证结果 (True/False/None, 决定性证据之一)
    """
    if scale.ndim != 2:
        return "未知", f"scale 非 2D: {tuple(scale.shape)}"
    G = scale.shape[0]

    # 反推两种可能的 group_size
    gs_full = weight_w / G          # 假设全宽
    gs_packed = 2 * weight_w / G    # 假设半宽打包 (K_full = 2*K_w)
    full_ok = abs(gs_full - round(gs_full)) < 1e-6 and round(gs_full) in COMMON_GROUP_SIZES
    pack_ok = abs(gs_packed - round(gs_packed)) < 1e-6 and round(gs_packed) in COMMON_GROUP_SIZES

    def _packed_verdict(tag, gs_val):
        """判定是否真打包: 形状自洽 + 4bit 解包验证 双证据闭合"""
        if unpack_ok is False:
            return f"Group-wise INT8 全宽 (伪 W4A4, {tag})", \
                   f"gs={gs_val}, 解包验证=全宽 int8 → 伪 W4A4"
        if unpack_ok is True:
            return "Packed INT4 半宽", f"gs={gs_val}, 解包验证=合法4bit"
        return "Packed INT4 半宽 (需确认)", \
               f"gs={gs_val}, 形状自洽但解包验证缺失"

    if gs_meta is not None:
        meta = int(gs_meta)
        consistent_full = (G * meta == weight_w)
        consistent_pack = (G * meta == 2 * weight_w)
        if consistent_full and full_ok:
            return "Group-wise INT8 全宽", f"gs={meta} 一致"
        if consistent_pack and pack_ok:
            return _packed_verdict("gs 元数据自洽", meta)
        # 元数据与 scale 反推都不一致 → 虚标
        if full_ok:
            return "Group-wise INT8 全宽 (gs 元数据虚标)", \
                   f"scale 反推 gs={round(gs_full)}, 元数据声称 {meta}"
        return "Group-wise 待定", f"scale 反推 gs={gs_full:.1f}, 元数据声称 {meta}, 均不常见"
    else:
        if full_ok and not pack_ok:
            return "Group-wise INT8 全宽", f"gs={round(gs_full)}"
        if pack_ok and not full_ok:
            return _packed_verdict("形状反推", round(gs_packed))
        if full_ok and pack_ok:
            return "Group-wise 待定", "全宽/半宽均可能, 需解包验证"
        return "Group-wise 待定", f"gs 反推不常见 ({gs_full:.1f} / {gs_packed:.1f})"


def parse_comfy_quant(t):
    """comfy_quant 是 JSON 字节编码, 尝试解码"""
    try:
        if t.dtype == torch.uint8 or t.dtype == torch.int8:
            s = bytes(t.tolist()).decode("utf-8")
            return json.loads(s)
    except Exception:
        pass
    return None


def compute_ratio(sd):
    """计算压缩比, 返回两种解读。

    - ratio_unbiased: 无偏解读 — 不信任 w4a4_group_size 元数据,
      int8/uint8 按全宽算 (×1), 保守下界 (防虚标污染判定)
    - ratio_packed: 打包解读 — int8/uint8 + 2D scale 按 4bit 打包算 (×2),
      若与无偏差异大说明量化层多为 4bit 打包
    - int32 weight: 每元素装 8 个 4bit → fp16 参考 ×8 (torchao 格式确定)
    返回 (ratio_unbiased, ratio_packed, total_gb, fp16_unbiased_gb)
    """
    total_bytes = 0
    fp16_unbiased = 0
    fp16_packed = 0
    for k, v in sd.items():
        if not isinstance(v, torch.Tensor):
            continue
        n = v.numel()
        total_bytes += n * v.element_size()
        mult_unbiased = 1
        mult_packed = 1
        if k.endswith(".weight"):
            base = k[: -len("weight")]
            if base + "weight_scale" in sd:
                if v.dtype == torch.int32:
                    mult_unbiased = 8
                    mult_packed = 8    # torchao: 每个 int32 装 8 个 4bit
                elif v.dtype in (torch.int8, torch.uint8):
                    # 打包解读: int8/uint8 伴生 weight_scale → 每字节 2 个 4bit
                    # (2D group scale 或 1D per-row scale 都可能打包, 由解包验证裁决)
                    mult_packed = 2
            elif base + "weight.absmax" in sd:
                # bitsandbytes NF4/FP4: uint8 打包 4bit + absmax scale + quant_map 码本
                # 打包解读: 每字节 2 个 4bit 值 → fp16 参考 ×4
                if v.dtype == torch.uint8:
                    mult_packed = 4
                # 无偏解读: int8/uint8 不修正 (假设全宽)
        fp16_unbiased += n * mult_unbiased * 2
        fp16_packed += n * mult_packed * 2
    if fp16_unbiased <= 0:
        return None, None, 0, 0
    r_u = total_bytes / fp16_unbiased * 100
    r_p = total_bytes / fp16_packed * 100 if fp16_packed > 0 else None
    return r_u, r_p, total_bytes / 1024**3, fp16_unbiased / 1024**3


def _fmt_elems(n):
    """元素数自适应格式化: B/M/K"""
    if n >= 1e9:
        return f"{n/1e9:.1f}B"
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.0f}K"
    return str(n)


def analyze_gguf(path):
    """GGUF 文件分析: 文件签名 + 关键 KV 元数据 + 张量类型分布 (不读张量数据区)。

    GGUF 布局: 文件签名(4B "GGUF") + version(u32) + tensor_count(u64) + metadata_kv_count(u64)
    → KV 元数据 → 张量信息表 (name/n_dims/dims/type/offset) → 张量数据区
    分块量化特点: Q4_K/Q6_K 等按 256 元素超块组织, scale/dmin 内嵌数据块
    (不同于 safetensors 的独立 scale 张量, 故不走 nibble/解包判据)
    """
    fsize = os.path.getsize(path) / 1024 ** 3
    type_count = Counter()
    type_elems = Counter()
    meta = {}
    ver = n_tensor = n_kv = 0
    try:
        with open(path, "rb") as f:
            sig = f.read(4)
            if sig != b"GGUF":
                print(f"[ERROR] 非 GGUF 文件签名: {sig!r}")
                return None
            ver, n_tensor, n_kv = struct.unpack("<IQQ", f.read(20))
            for _ in range(n_kv):
                k = _gguf_read_string(f)
                vtype = struct.unpack("<I", f.read(4))[0]
                v = _gguf_read_value(f, vtype)
                if k in ("general.architecture", "general.name", "general.file_type",
                         "general.size_label", "general.quantization_version",
                         "general.parameter_count"):
                    meta[k] = v
            for _ in range(n_tensor):
                _name = _gguf_read_string(f)
                nd = struct.unpack("<I", f.read(4))[0]
                dims = struct.unpack(f"<{nd}Q", f.read(8 * nd))
                t = struct.unpack("<I", f.read(4))[0]
                _offset = struct.unpack("<Q", f.read(8))[0]
                type_count[t] += 1
                n = 1
                for d in dims:
                    n *= d
                type_elems[t] += n
    except Exception as e:
        print(f"[ERROR] GGUF 解析失败: {e}")
        return None

    total_t = sum(type_count.values())
    total_elems = sum(type_elems.values())
    est_bytes = 0.0
    fp16_bytes = 0
    ratio = None
    dist_lines = []
    for t, c in type_count.most_common():
        name = GGML_TYPE_NAMES.get(t, f"type{t}")
        bits = GGML_TYPE_BITS.get(t, 16)
        n = type_elems[t]
        est_bytes += n * bits / 8
        fp16_bytes += n * 2
        share_e = n / total_elems * 100 if total_elems else 0
        dist_lines.append((name, c, _fmt_elems(n), share_e, bits))
    if fp16_bytes > 0:
        ratio = est_bytes / fp16_bytes * 100

    # 主导按权重元素数计算 (张量个数会被大量小张量如 norm 干扰)
    dom_t = max(type_elems, key=type_elems.get) if type_elems else None
    dom_name = GGML_TYPE_NAMES.get(dom_t, f"type{dom_t}") if dom_t is not None else "?"
    dom_elems = type_elems.get(dom_t, 0)
    share = dom_elems / total_elems * 100 if total_elems else 0
    arch = meta.get('general.architecture', '?')
    type_str = f"GGUF v{ver} {arch} ({dom_name} 主导)"
    load_advice = "llama.cpp 系 (ollama / llama-server / GGUF 原生)"

    # ===== 渲染三区 =====
    print_file_head(fsize, path)
    print_sec_quick(type_str, load_advice, fsize, ratio)

    # 📊 性能与结构评估
    w_bits = GGML_TYPE_BITS.get(dom_t, 16)
    if fp16_bytes > 0:
        if LANG == "en":
            protocol = f"GGUF v{ver} block-quantized"
            algo = f"block-wise scale embedded (256-elem superblock, {dom_name} dominant)"
            mechanism = f"block quantization: {dom_name} block-wise scaling (block-shared scale/dmin)"
            save_note = "GGUF block-quantized"
            w_text = f"{round(w_bits)} bit ({dom_name} dominant)"
            a_text = "A16 primary (llama.cpp default), A8 optional"
        else:
            protocol = f"GGUF v{ver} 分块量化"
            algo = f"块级 scale 内嵌 (256 元素超块, {dom_name} 主导)"
            mechanism = f"分块量化：{dom_name} 块级缩放（块内共享 scale/dmin）"
            save_note = "GGUF 分块量化"
            w_text = f"{round(w_bits)} bit（{dom_name} 主导）"
            a_text = "A16 为主（llama.cpp 默认）, A8 可选"
        print_sec_perf(fp16_bytes / 1024 ** 3, ratio, protocol, algo, save_note, mechanism)
        meta_a = f"architecture={arch}"
        if meta.get('general.file_type') is not None:
            meta_a += f", file_type={meta['general.file_type']}"
        _print_activation_block(meta_a, os.path.splitext(path)[1], w_text, a_text)

    print_sec_diag()
    meta_parts = [f"architecture={arch}"]
    for k in ("general.name", "general.file_type", "general.quantization_version"):
        if k in meta:
            meta_parts.append(f"{k.split('.')[-1]}={meta[k]}")
    if LANG == "en":
        print(" [GGUF Container & Tensor Types]")
        print(f"  • signature: GGUF | version v{ver} | tensors {n_tensor} | KV metadata {n_kv}")
        print(f"  • metadata: " + " | ".join(meta_parts))
        print(f"  • type distribution (by weight elements):")
        for name, c, ne, se, bits in dist_lines:
            print(f"      {name:<10}: {c} tensors  ({ne} elems, {se:.1f}% weight, ~{bits:.1f} bit/elem)")
        if ratio is not None:
            print(f"  • est. ratio: ~{ratio:.0f}% ({est_bytes/1024**3:.2f} GB vs full fp16 ~{fp16_bytes/1024**3:.2f} GB)")
    else:
        print(" [GGUF 容器与张量类型]")
        print(f"  • 文件签名: GGUF | 版本 v{ver} | 张量 {n_tensor} 个 | KV 元数据 {n_kv} 项")
        print(f"  • 元数据: " + " | ".join(meta_parts))
        print(f"  • 类型分布 (按权重元素占比):")
        for name, c, ne, se, bits in dist_lines:
            print(f"      {name:<10}: {c} 张量  ({ne} 元素, {se:.1f}% 权重, ~{bits:.1f} bit/元素)")
        if ratio is not None:
            print(f"  • 压缩比估算: ~{ratio:.0f}% ({est_bytes/1024**3:.2f} GB vs 全 fp16 ~{fp16_bytes/1024**3:.2f} GB)")

    # 📋 文件名审计 (GGUF 架构精确, 可完整核验)
    ident_g, quant_claim_g = _strip_quant_from_name(os.path.splitext(os.path.basename(path))[0])
    std_quant_g = _standard_quant_tag(type_str, None, dom_name)
    _run_audit(os.path.basename(path), ident_g, quant_claim_g, std_quant_g,
               _arch_from_name(path), arch, total_elems)

    print_sec_final()
    print(f"  → {_l('f_type_id')}: {_type_en(type_str) if LANG == 'en' else type_str}")
    if LANG == "en":
        print(f"  → {_l('f_basis')}: GGUF signature + block-quant type distribution, {dom_name} {share:.0f}% weight elems ({type_count.get(dom_t, 0)} tensors)")
    else:
        print(f"  → {_l('f_basis')}: GGUF 文件签名 + 分块量化类型分布, {dom_name} 占 {share:.0f}% 权重元素 ({type_count.get(dom_t, 0)} 张量)")
    if len(type_count) > 1:
        others = ", ".join(f"{GGML_TYPE_NAMES.get(t, t)}×{c}" for t, c in type_count.most_common(6)[1:])
        if LANG == "en":
            print(f"  → Mixed composition: {others}")
        else:
            print(f"  → 混合构成: {others}")
    print(f"  → {_l('f_load_advice')}: {load_advice}")
    if ratio is not None:
        if LANG == "en":
            print(f"  → {_l('f_struct_pt')}: est. ratio ~{ratio:.0f}%, {total_t} tensors, block-wise scale embedded")
        else:
            print(f"  → {_l('f_struct_pt')}: 估算压缩比 ~{ratio:.0f}%, {total_t} 个张量, 块级 scale 内嵌数据块")
    print(BANNER)
    return {
        "path": path, "size_gb": fsize, "ratio": ratio,
        "ratio_packed": None, "type": type_str, "verdicts": [],
    }


def analyze_file(path):
    """分析单个模型文件: .safetensors → 量化格式; .gguf → 分块量化"""
    if not os.path.exists(path):
        print(f"[ERROR] 文件不存在: {path}")
        return None
    if path.lower().endswith(".gguf"):
        return analyze_gguf(path)
    if not path.lower().endswith(".safetensors"):
        print(f"[SKIP] 非 safetensors/gguf 文件: {os.path.basename(path)}")
        return None

    fsize = os.path.getsize(path) / 1024 ** 3
    sd = st.load_file(path, device="cpu")
    n_keys = len(sd)
    verdicts = []  # (维度, 判断, 证据)

    # 1. 后缀统计
    suffix = Counter()
    for k in sd:
        suf = k.rsplit(".", 1)[-1] if "." in k else "(none)"
        suffix[suf] += 1
    if suffix.get("weight_scale", 0):
        verdicts.append(("key", "含 weight_scale 伴生张量", f"{suffix['weight_scale']} 个"))
    if suffix.get("comfy_quant", 0):
        verdicts.append(("key", "含 comfy_quant 元数据", f"{suffix['comfy_quant']} 个 → ComfyUI 量化协议"))
    if suffix.get("weight_zp", 0):
        verdicts.append(("key", "含 weight_zp (zero-point)", f"{suffix['weight_zp']} 个"))
    if suffix.get("w4a4_group_size", 0):
        verdicts.append(("key", "含 w4a4_group_size", "→ 需结合权重宽度判断 (可能真 INT4 或伪 W4A4)"))
    if suffix.get("absmax", 0) and suffix.get("quant_map", 0):
        verdicts.append(("key", "含 bnb 量化伴生 (absmax+quant_map)",
                         f"{suffix['absmax']} 组 → bitsandbytes NF4/FP4 体系"))
    if suffix.get("bitsandbytes__nf4", 0):
        verdicts.append(("key", "含 bitsandbytes__nf4 标记",
                         f"{suffix['bitsandbytes__nf4']} 个 → NF4 决定性证据"))

    # 2. dtype 统计 (排除 1 维标量元数据)
    dt = Counter()
    scalar_meta = Counter()  # shape==(1,) 的标量元数据 (如 w4a4_group_size)
    for k, v in sd.items():
        if not isinstance(v, torch.Tensor):
            continue
        if v.ndim == 0 or (v.ndim == 1 and v.numel() <= 4):
            scalar_meta[str(v.dtype)] += 1
            continue
        dt[str(v.dtype)] += 1
    if dt.get("torch.int8", 0) > 0:
        verdicts.append(("dtype", "存在 int8 张量", f"{dt['torch.int8']} 个"))
    big_int32 = any(k.endswith(".weight") and v.dtype == torch.int32 and v.ndim >= 2
                    for k, v in sd.items() if isinstance(v, torch.Tensor))
    if big_int32:
        verdicts.append(("dtype", "存在 int32 打包权重 (疑似 torchao)", "int32 weight + scale"))
    if dt.get("torch.bfloat16", 0) > 0:
        verdicts.append(("dtype", "存在 bf16 (未量化层)", f"{dt['torch.bfloat16']} 个"))

    # 3. 压缩比 (双解读) — 提前计算, 供层分布做半宽/全宽裁决
    ratio, ratio_packed, total_gb, fp16_gb = compute_ratio(sd)
    if ratio is not None:
        if ratio_packed is not None and abs(ratio_packed - ratio) > 5:
            # 两种解读差异明显 → 量化层可能是 4bit 打包, 由结构证据裁决
            verdicts.append(("压缩", f"无偏{ratio:.0f}%/打包{ratio_packed:.0f}%", "双解读差异大"))
        elif ratio < 35:
            verdicts.append(("压缩", "真 INT4 级", f"{ratio:.0f}%"))
        elif ratio < 55:
            verdicts.append(("压缩", "INT8 级", f"{ratio:.0f}%"))
        elif ratio < 75:
            verdicts.append(("压缩", "混合精度", f"{ratio:.0f}%"))
        else:
            verdicts.append(("压缩", "接近未量化", f"{ratio:.0f}%"))

    # 4. 典型量化层结构 + 全模型格式分布
    ws_keys = [k for k in sd if k.endswith(".weight_scale")]
    bnb_keys = [k for k in sd if k.endswith(".weight.absmax")]
    layer_types = Counter()
    unpack_evidence = None   # True=解包验证4bit合法 / False=全宽 / None=未测
    unpack_sample = None     # (key, 打包, 字节unique, lo, hi)
    first_key = None
    first_detail = None
    if ws_keys or bnb_keys:
        # bitsandbytes NF4/FP4 层 (absmax + quant_map 码本体系, 无 weight_scale)
        for k in bnb_keys[:80]:
            base = k[: -len(".weight.absmax")]
            w = sd.get(base + ".weight")
            s = sd[k]
            if w is None or not isinstance(w, torch.Tensor):
                continue
            label = "NF4 (bitsandbytes)"
            det = f"weight {tuple(w.shape)} {w.dtype}, absmax {tuple(s.shape)} 1D块级, quant_map 16值码本"
            layer_types[label] += 1
            if first_detail is None:
                first_key, first_detail = k, det
        for k in ws_keys[:80]:  # 采样前 80 层, 大模型足够代表
            base = k[: -len("weight_scale")]
            w = sd.get(base + "weight")
            s = sd[k]
            if w is None or not isinstance(w, torch.Tensor):
                continue
            gs_meta = None
            gsk = base + "w4a4_group_size"
            if gsk in sd:
                gv = sd[gsk]
                try:
                    gs_meta = int(gv.item())
                except Exception:
                    gs_meta = None
            N, K_w = w.shape[0], w.shape[1]
            if w.dtype == torch.int32:
                label, det = "torchao int32 打包", f"weight {N}x{K_w} int32, scale {tuple(s.shape)}"
            elif s.ndim == 1:
                # 1D scale 也可能是 per-row int4 打包 (无 group_size), 需解包验证
                if w.dtype in (torch.int8, torch.uint8) and unpack_evidence is None:
                    ok, bu, lo_u, hi_u = verify_unpack4(w)
                    unpack_evidence = ok
                    unpack_sample = (k, ok, bu, lo_u, hi_u)
                if unpack_evidence is True:
                    label, det = "INT4 per-row 打包 (非 nunchaku)", \
                                 f"weight {N}x{K_w} {w.dtype}, scale 1D, 解包=合法4bit"
                elif s.shape[0] == N:
                    label, det = "INT8 per-row", f"weight {N}x{K_w} {w.dtype}, scale 1D"
                else:
                    label, det = "tensorwise/其它", f"weight {N}x{K_w}, scale {tuple(s.shape)}"
            elif s.ndim == 2 and 1 in s.shape:
                # scale 形如 (N,1)/(1,N): tensorwise 或 per-row 的列向量存储
                if w.dtype in (torch.int8, torch.uint8) and unpack_evidence is None:
                    ok, bu, lo_u, hi_u = verify_unpack4(w)
                    unpack_evidence = ok
                    unpack_sample = (k, ok, bu, lo_u, hi_u)
                if unpack_evidence is True:
                    label, det = "INT4 tensorwise 打包", \
                                 f"weight {N}x{K_w} {w.dtype}, scale {tuple(s.shape)}, 解包=4bit"
                elif s.shape[0] == N or s.shape[1] == N:
                    label, det = "INT8 tensorwise/per-row", \
                                 f"weight {N}x{K_w} {w.dtype}, scale {tuple(s.shape)}"
                else:
                    label, det = "tensorwise/其它", f"weight {N}x{K_w}, scale {tuple(s.shape)}"
            elif s.ndim == 2:
                # 对 int8/uint8 2D-scale 层做 4bit 解包验证 (决定性证据)
                if w.dtype in (torch.int8, torch.uint8) and unpack_evidence is None:
                    ok, bu, lo_u, hi_u = verify_unpack4(w)
                    unpack_evidence = ok
                    unpack_sample = (k, ok, bu, lo_u, hi_u)
                label, det = infer_group_size(K_w, s, gs_meta=gs_meta, ratio=ratio,
                                              numeric_packed=unpack_evidence,
                                              unpack_ok=unpack_evidence)
                det = f"weight {N}x{K_w} {w.dtype}, scale {tuple(s.shape)}, {det}"
            else:
                label, det = "未知", f"scale {tuple(s.shape)}"
            layer_types[label] += 1
            if first_detail is None:
                first_key, first_detail = k, det

        # 主结构判定 (取占比最高的)
        dominant = layer_types.most_common(1)[0][0]
        if "Packed INT4" in dominant:
            verdicts.append(("结构", "Packed INT4 (半宽打包)", f"{layer_types[dominant]} 层主导"))
        elif "Group-wise INT8 全宽 (gs 元数据虚标)" in dominant or "伪 W4A4" in dominant:
            verdicts.append(("结构", "伪 W4A4 (group-wise INT8)", f"{layer_types[dominant]} 层主导, gs 虚标"))
        elif "Group-wise INT8" in dominant:
            verdicts.append(("结构", "Group-wise INT8", f"{layer_types[dominant]} 层主导"))
        elif "INT4 per-row 打包" in dominant or "INT4 tensorwise 打包" in dominant:
            verdicts.append(("结构", "INT4 per-row 打包", f"{layer_types[dominant]} 层主导"))
        elif "INT8 tensorwise" in dominant:
            verdicts.append(("结构", "INT8 tensorwise", f"{layer_types[dominant]} 层主导"))
        elif "INT8 per-row" in dominant:
            verdicts.append(("结构", "INT8 per-row", f"{layer_types[dominant]} 层主导"))
        elif "NF4" in dominant:
            verdicts.append(("结构", "NF4 (bitsandbytes)", f"{layer_types[dominant]} 层主导"))
        elif "torchao" in dominant.lower():
            verdicts.append(("结构", "torchao int32 打包", f"{layer_types[dominant]} 层主导"))
        else:
            verdicts.append(("结构", f"待定 ({dominant})", f"{layer_types[dominant]} 层主导"))
    else:
        for k in sd:
            if k.endswith(".weight"):
                first_key = k
                first_detail = f"weight {tuple(sd[k].shape)} {sd[k].dtype}"
                break
        verdicts.append(("结构", "无 weight_scale", "可能未量化 或 GGUF 另行处理"))

    # 5. comfy_quant 解析
    comfy_quant_info = None
    cq_keys = [k for k in sd if k.endswith(".comfy_quant")]
    if cq_keys:
        info = parse_comfy_quant(sd[cq_keys[0]])
        if info:
            comfy_quant_info = info
            fmt = info.get("format", "?")
            verdicts.append(("元数据", f"comfy_quant.format={fmt}", ""))
            if info.get("group_size"):
                verdicts.append(("元数据", f"group_size={info['group_size']}", ""))
            if info.get("quarot"):
                verdicts.append(("元数据", "quarot 旋转", ""))

    # ===== 综合判定 =====
    dominant = layer_types.most_common(1)[0][0] if layer_types else None
    res = final_verdict(sd, verdicts, comfy_quant_info, ratio,
                        layer_types, dominant, unpack_evidence, ratio_packed)
    type_str = res["type"]

    # ===== 渲染三区 =====
    print_file_head(fsize, path)
    print_sec_quick(type_str, res["load"], fsize, res["final_ratio"])

    # 📊 性能与结构评估
    if comfy_quant_info:
        protocol = f"comfy_quant (format: {comfy_quant_info.get('format', '?')})"
    elif type_str.startswith("NF4"):
        protocol = "bitsandbytes NF4 (absmax + quant_map 码本)"
    elif "Packed INT4" in type_str:
        protocol = "字节打包 INT4 (每字节 2×4bit)"
    elif "TorchAO" in type_str:
        protocol = "torchao (int32 打包)"
    elif "INT8" in type_str or "Group-wise" in type_str or "ComfyUI" in type_str:
        protocol = "ComfyUI 量化体系"
    elif "接近未量化" in type_str:
        protocol = "无量化（原生 FP16/BF16）"
    elif "混合精度" in type_str:
        protocol = "部分层量化（无统一协议）"
    else:
        protocol = type_str
    algo = ""
    if comfy_quant_info and comfy_quant_info.get("convrot"):
        algo = f"QuaRot 旋转优化 (convrot_groupsize={comfy_quant_info.get('convrot_groupsize', '?')})"
    elif type_str.startswith("NF4"):
        algo = "NF4 非线性码本 (16 值, 块级 absmax scale)"
    elif "Packed INT4" in type_str:
        algo = "group-wise 字节打包" if "group-wise" in type_str else "per-row 字节打包"
    elif "TorchAO" in type_str:
        algo = "int32 打包 (每 int32 装 8×4bit)"
    elif "Group-wise INT8" in type_str:
        algo = "group-wise INT8 (全宽 2D scale)"
    elif "INT8 tensorwise" in type_str:
        algo = "tensorwise 标量 scale"
    elif "INT8 per-row" in type_str:
        algo = "per-row 1D scale"
    if type_str.startswith("NF4"):
        save_note = "NF4 4bit 码本打包"
    elif "INT4" in type_str or "TorchAO" in type_str:
        save_note = "4bit 打包"
    elif "INT8" in type_str or "Group-wise" in type_str:
        save_note = "标准全宽 INT8，无打包"
    elif "接近未量化" in type_str:
        save_note = "无压缩，FP16/BF16 原生"
    elif "混合精度" in type_str:
        save_note = "部分量化"
    else:
        save_note = "量化压缩"
    if fp16_gb:
        mechanism = _quant_mechanism(type_str, comfy_quant_info)
        print_sec_perf(fp16_gb, res["final_ratio"], protocol, algo, save_note, mechanism)
        meta_a, w_a, a_a = _activation_derive_safetensors(sd, type_str, comfy_quant_info)
        _print_activation_block(meta_a, os.path.splitext(path)[1], w_a, a_a)

    print_sec_diag()
    print(" " + _l("sub_tensor"))
    print(f"  • 总 Key 数: {n_keys}")
    print(f"  • Key 后缀: " + " | ".join(f".{s}: {c}" for s, c in suffix.most_common(8)))
    print(f"  • dtype: " + " | ".join(f"{d}: {c}" for d, c in dt.most_common()))
    if scalar_meta:
        if LANG == "en":
            print(f"  • scalar metadata {dict(scalar_meta)} excluded (e.g. w4a4_group_size)")
        else:
            print(f"  • 标量元数据 {dict(scalar_meta)} 已排除 (如 w4a4_group_size)")
    print()

    print(" " + _l("sub_ratio"))
    if ratio is not None:
        print(f"  • 实际数据: {total_gb:.2f} GB | 全 FP16 基准: ~{fp16_gb:.2f} GB")
        if ratio_packed is not None and abs(ratio_packed - ratio) > 5:
            if LANG == "en":
                print(f"  • dual: {ratio:.0f}% (unbiased/full)  vs  {ratio_packed:.0f}% (if 4bit packed)  [gap {abs(ratio_packed - ratio):.0f}pt]")
            else:
                print(f"  • 双解读: {ratio:.0f}% (无偏/全宽)  vs  {ratio_packed:.0f}% (若4bit打包)  [差异 {abs(ratio_packed - ratio):.0f}pt]")
        else:
            print(f"  • 压缩率: {ratio:.0f}%")
        if LANG == "en":
            print(f"  • verdict: {res['final_ratio']:.0f}% (by final type {_type_en(type_str)})")
        else:
            print(f"  • 裁决: {res['final_ratio']:.0f}% (按最终类型 {type_str} 选取)")
    print()

    print(" " + _l("sub_sample"))
    if layer_types:
        print(f"  • 格式分布: " + " | ".join(f"{l}: {c} 层" for l, c in layer_types.most_common()))
        if unpack_sample is not None:
            print(f"  • 解包采样: {unpack_sample[0]}")
            print(f"      └─ 打包={unpack_sample[1]} | 字节unique={unpack_sample[2]} | lo={unpack_sample[3]} hi={unpack_sample[4]}")
        if first_key and first_detail:
            print(f"  • 示例: {first_key}")
            print(f"      └─ {first_detail}")
    else:
        if LANG == "en":
            print("  • no .weight_scale / .weight.absmax companion tensors")
        else:
            print("  • 无 .weight_scale / .weight.absmax 伴生张量")
        if first_detail:
            print(f"  • 示例 weight: {first_detail}")
    if comfy_quant_info:
        print(f"  • comfy_quant: {json.dumps(comfy_quant_info, ensure_ascii=False)}")
    print()

    # 📋 文件名审计 (safetensors 架构家族级推断)
    ident_f, quant_claim_f = _strip_quant_from_name(os.path.splitext(os.path.basename(path))[0])
    std_quant_f = _standard_quant_tag(type_str, comfy_quant_info)
    # 参数量: 用 fp16 等效基准反推 (与 compute_ratio 同口径, 含 int32 打包 ×8 修正)
    w_elems = int(fp16_gb * 1024 ** 3 / 2) if fp16_gb else 0
    _run_audit(os.path.basename(path), ident_f, quant_claim_f, std_quant_f,
               _arch_from_name(path), _arch_from_keys(sd), w_elems)

    print_sec_final()
    for dim, v, e in verdicts:
        if LANG == "en":
            dim_en = {"key": "key", "dtype": "dtype", "压缩": "ratio",
                      "结构": "structure", "元数据": "metadata"}.get(dim, dim)
            line = f"  [{dim_en}] {_verdict_en(v)}"
            if e:
                line += f"  ({_verdict_en(e)})"
        else:
            line = f"  [{dim}] {v}"
            if e:
                line += f"  ({e})"
        print(line)
    print(SECTION)
    print(f"  → {_l('f_type_id')}: {_type_en(type_str) if LANG == 'en' else type_str}")
    for ev in res["evidence"]:
        print(f"  → {_l('f_basis')}: {_verdict_en(ev) if LANG == 'en' else ev}")
    if res["load"]:
        print(f"  → {_l('f_load_advice')}: {_load_en(res['load']) if LANG == 'en' else res['load']}")
    if res["extra"]:
        extra_t = [_verdict_en(x) if LANG == "en" else x for x in res["extra"]]
        print(f"  → {_l('f_struct_pt')}: " + "; ".join(extra_t))
    if res["conflicts"]:
        if LANG == "en":
            print("  ⚠ Evidence conflicts:")
        else:
            print("  ⚠ 证据冲突:")
        for c in res["conflicts"]:
            print(f"      - {c}")
    print(BANNER)
    return {
        "path": path,
        "size_gb": fsize,
        "ratio": ratio,
        "ratio_packed": ratio_packed,
        "type": type_str,
        "verdicts": verdicts,
    }


def final_verdict(sd, verdicts, comfy_quant_info, ratio, layer_types, dominant,
                  unpack_evidence=None, ratio_packed=None):
    """综合判定: 结论必须由「主导格式」+ 决定性验证推导, 证据链闭合。

    只对模型本身下结论 (格式类型 + 结构参数), 不输出任何插件可用性判断。
    - dominant: 采样层中出现最多的结构标签 (None = 无量化层)
    - 少数层异常不推翻主导判定, 只追加警告
    - 决定性信号 (comfy_quant / int32+weight_zp / bnb NF4) 优先级高于结构占比
    返回 dict: {type, final_ratio, evidence, load, conflicts, extra}
    """
    has_comfy_quant = any(k.endswith(".comfy_quant") for k in sd)
    has_nf4 = any(k.endswith(".bitsandbytes__nf4") for k in sd)
    has_zp = any(k.endswith(".weight_zp") for k in sd)
    big_int32 = any(k.endswith(".weight") and v.dtype == torch.int32 and v.ndim >= 2
                    for k, v in sd.items() if isinstance(v, torch.Tensor))
    lt = layer_types or {}
    n_total = sum(lt.values())

    def share_of(substr):
        """返回包含子串的标签占总采样层的比例"""
        n = sum(c for l, c in lt.items() if substr in l)
        return n / n_total if n_total else 0

    dom = dominant or ""
    conflicts = []
    extra = []
    evidence = []

    # --- 决定性信号 0: bitsandbytes NF4 (quant_state 标记, 物理证据) ---
    if has_nf4:
        n_nf4 = len([k for k in sd if k.endswith(".weight.absmax")])  # 全量 NF4 层数
        n_plain = sum(1 for k, v in sd.items() if isinstance(v, torch.Tensor)
                      and k.endswith(".weight")
                      and v.dtype in (torch.bfloat16, torch.float16, torch.float32))
        type_str = "NF4 (bitsandbytes) 量化"
        evidence.append("bitsandbytes__nf4 quant_state 标记 + absmax/quant_map 码本 (决定性物理证据)")
        extra.append(f"NF4 量化层 {n_nf4} 层 + 未量化 {n_plain} 层 (部分量化)")
        if n_total > 0:
            extra.append(f"采样结构: {dom}")

    # --- 决定性信号 1: comfy_quant (ComfyUI 官方量化协议, 按 format 细分) ---
    elif has_comfy_quant:
        fmt = (comfy_quant_info or {}).get("format", "") or "unknown"
        fl = fmt.lower()
        # comfy_quant 是 ComfyUI 主仓库的量化元数据协议 (comfy/ops.py QUANT_ALGOS),
        # format 字段是官方注册的格式名。按名称细分, 不做来源猜测。
        if "int8" in fl:
            type_str = f"ComfyUI INT8 ({fmt})"
            extra.append("ComfyUI 原生 int8 量化 (QUANT_ALGOS 注册格式)")
            if comfy_quant_info and comfy_quant_info.get("convrot"):
                extra.append("带 QuaRot 旋转 (convrot_groupsize="
                             f"{comfy_quant_info.get('convrot_groupsize', '?')})")
        elif "int4" in fl or "tint4" in fl:
            type_str = f"ComfyUI INT4 ({fmt})"
            extra.append("int32 打包 + weight_scale/zp 伴生 (TINT4/ComfyUI 协议)")
        elif ("fp8" in fl or "float8" in fl):
            type_str = f"ComfyUI FP8 ({fmt})"
            extra.append("float8 权重 + scale 伴生")
        elif "nf4" in fl:
            type_str = f"NF4 ({fmt})"
            extra.append("NF4 量化, 非字节打包布局")
        else:
            type_str = f"ComfyUI 量化 ({fmt})"
            extra.append(f"comfy_quant.format={fmt} (ComfyUI QUANT_ALGOS 注册格式)")
        evidence.append("comfy_quant 专有元数据 (ComfyUI 官方量化协议, 非 torchao)")
        if n_total > 0:
            extra.append(f"量化层 {n_total} 层主导: {dom}")

    # --- 决定性信号 2: int32 打包 + weight_zp (非对称 INT4) ---
    elif has_zp and big_int32:
        type_str = "非对称 INT4 (含 zero-point + int32 打包)"
        evidence.append("weight_zp + int32 打包张量")
        extra.append("带 zero-point, 非对称量化")

    # --- 主导格式: Packed INT4 (需 主导占比 + 解包验证 双证据) ---
    elif share_of("Packed INT4") >= 0.5 and unpack_evidence is True:
        type_str = "Packed INT4 (group-wise, nunchaku 风格)"
        evidence.append("半宽 int8/uint8 字节打包权重 + 2D group scale, 主导层占比 "
                        f"{share_of('Packed INT4')*100:.0f}%, 解包验证=合法4bit")
        extra.append("字节打包布局 (每字节 2 个 4bit), 含 group_size 元数据")

    # --- Packed INT4 形状自洽但证据不足: 降级提示 ---
    elif share_of("Packed INT4") >= 0.5:
        type_str = "疑似 Packed INT4 (证据不足, 保守按 INT8 处理)"
        evidence.append(f"形状半宽自洽, 但解包验证缺失/失败: {unpack_evidence} (需 4bit 值域合法)")
        extra.append("建议结合转换工具日志或人工复核确认")

    # --- 主导格式: 伪 W4A4 / group-wise INT8 ---
    elif "虚标" in dom or "伪 W4A4" in dom:
        type_str = "Group-wise INT8 (伪 W4A4 / 命名不符)"
        evidence.append("全宽 INT8 权重 + w4a4_group_size 误导性元数据, "
                        f"主导 {dom} ({share_of('虚标')*100:.0f}%)")
        extra.append("gs 元数据与 scale 布局不匹配, 实际为 8bit 权重")

    # --- 主导格式: Group-wise INT8 (真 group-wise, 非虚标) ---
    elif "Group-wise INT8" in dom:
        type_str = "Group-wise INT8"
        evidence.append("全宽 INT8 权重 + 2D scale, group_size 自洽")
        extra.append("2D [G,N] scale, group_size 与权重宽度匹配")

    # --- 主导格式: INT4 per-row 打包 (无 group_size) ---
    elif "INT4 per-row 打包" in dom or "INT4 tensorwise 打包" in dom:
        type_str = "INT4 per-row 打包 (无 group_size)"
        evidence.append("1D per-row scale + 解包验证=合法4bit, 但无 group_size/2D scale")
        extra.append("per-row 布局, 每输出行一个 scale")

    # --- 主导格式: INT8 tensorwise (列向量 scale) ---
    elif "INT8 tensorwise" in dom:
        type_str = "INT8 tensorwise/per-row"
        evidence.append("scale 形如 (N,1) 列向量, 每行一个 scale, 无打包")
        extra.append("1D 语义 scale, tensorwise 或 per-row 布局")

    # --- 主导格式: INT8 per-row ---
    elif "INT8 per-row" in dom:
        type_str = "INT8 per-row / INT8+BF16 混合"
        evidence.append("1D per-row scale, 无位打包结构")
        extra.append("1D scale [N], 每行一个 scale")

    # --- 主导格式: torchao int32 ---
    elif "torchao" in dom.lower():
        type_str = "TorchAO INT4 (int32 打包)"
        evidence.append("int32 打包权重主导 (但无 comfy_quant 元数据)")
        extra.append("int32 打包 + 2D scale, 疑似 torchao affine 布局")

    # --- 无量化层: 看压缩比 ---
    elif n_total == 0 and ratio is not None:
        if ratio >= 75:
            # 动态显示未量化层实际主 dtype
            dcount = Counter()
            for k, v in sd.items():
                if isinstance(v, torch.Tensor) and v.ndim >= 2:
                    dcount[str(v.dtype)] += 1
            main_dt = dcount.most_common(1)[0][0] if dcount else ""
            if "float16" in main_dt:
                type_str = "接近未量化 (fp16 为主)"
            elif "bfloat16" in main_dt:
                type_str = "接近未量化 (bf16 为主)"
            else:
                type_str = "接近未量化"
            evidence.append(f"压缩比 {ratio:.0f}% ≥75%, 无量化伴生张量")
            extra.append("压缩比 ≥75%, 无量化伴生张量")
        elif ratio < 55:
            type_str = "INT8 / INT8+BF16 混合 (无伴生 scale)"
            evidence.append("无 weight_scale 但压缩比显著")
            extra.append("压缩比显著但无 scale 结构, 可能为全局/tensorwise 量化")
        else:
            type_str = "混合精度 (部分层量化)"
            evidence.append(f"压缩比 {ratio:.0f}% 中等, 部分层量化")
            extra.append("压缩比中等, 部分层量化")

    # --- 兜底 ---
    else:
        type_str = "格式不明确"
        evidence.append("无法归入已知量化格式" + (f", 主导格式 {dom}" if dom else ""))
        extra.append("无法归入已知量化格式")

    # --- 通用加载建议 (仅基于格式本身, 不绑定插件) ---
    load_advice = ""
    if type_str.startswith("ComfyUI INT8"):
        load_advice = "ComfyUI 原生加载 (QUANT_ALGOS 内置, 无需第三方节点)"
    elif type_str.startswith("ComfyUI INT4"):
        load_advice = "ComfyUI 原生 / TINT4 节点 (comfy_quant 协议)"
    elif type_str.startswith("ComfyUI FP8") or type_str.startswith("ComfyUI 量化"):
        load_advice = "ComfyUI 原生加载 (QUANT_ALGOS 内置)"
    elif type_str.startswith("NF4"):
        load_advice = "bitsandbytes NF4 加载器 (bnb 原生, 非 ComfyUI 协议)"
    elif "Packed INT4" in type_str and "group-wise" in type_str:
        load_advice = "支持字节打包 INT4 的后端 (如 nunchaku 系)"
    elif "INT4 per-row" in type_str:
        load_advice = "支持 per-row INT4 打包的后端 (非标准 nunchaku 布局)"
    elif "INT8" in type_str or "Group-wise INT8" in type_str:
        load_advice = "ComfyUI 原生 int8 加载"
    elif type_str.startswith("接近未量化") or type_str.startswith("混合精度"):
        load_advice = "标准 fp16/bf16 加载器"

    # --- 冲突检查: 决定性信号 vs 结构占比不一致时警告 ---
    if has_comfy_quant and n_total > 0:
        fmt = ((comfy_quant_info or {}).get("format", "") or "").lower()
        doml = dom.lower()
        expected = None
        if "int8" in fmt:
            expected = ["int8", "torchao"]
        elif "int4" in fmt or "tint4" in fmt:
            expected = ["int4", "torchao", "int32"]
        elif ("fp8" in fmt or "float8" in fmt):
            expected = ["fp8", "torchao"]
        if expected and not any(e in doml for e in expected):
            conflicts.append(f"comfy_quant.format={fmt} 与结构主导 [{dom}] 不一致")
    if has_zp and big_int32 and share_of("torchao") < 0.5 and n_total > 0:
        conflicts.append(f"weight_zp+int32 存在但结构主导为 [{dom}]")
    if share_of("Packed INT4") > 0 and share_of("Packed INT4") < 0.5 and "Packed INT4" in type_str:
        conflicts.append(f"Packed INT4 占比仅 {share_of('Packed INT4')*100:.0f}%, 非绝对主导")

    # --- 裁决压缩比: 打包类格式取打包解读, 其余取无偏解读 ---
    final_ratio = ratio
    packed_like = any(x in type_str for x in ("Packed INT4", "INT4", "NF4", "TorchAO", "非对称 INT4"))
    if packed_like and ratio_packed is not None:
        final_ratio = ratio_packed

    return {"type": type_str, "final_ratio": final_ratio, "evidence": evidence,
            "load": load_advice, "conflicts": conflicts, "extra": extra}


def main(args):
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    # 过滤 --lang 开关参数
    raw = args[1:]
    targets = []
    skip_next = False
    for a in raw:
        if a == "--lang":
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        targets.append(a)
    files = []
    for t in targets:
        if os.path.isdir(t):
            for root, _, fns in os.walk(t):
                for fn in fns:
                    if fn.lower().endswith((".safetensors", ".gguf")):
                        files.append(os.path.join(root, fn))
        else:
            files.append(t)

    if not files:
        print("[ERROR] " + _l("no_files"))
        sys.exit(1)

    results = []
    for f in files:
        r = analyze_file(f)
        if r:
            results.append(r)

    # 汇总表
    if len(results) > 1:
        print("\n" + "=" * 78)
        if LANG == "en":
            print("Batch Scan Summary")
            print(f"{'Model':<44} {'Size':>7} {'unbiased%':>9} {'packed%':>8}  Verdict")
        else:
            print("【批量扫描汇总】")
            print(f"{'模型':<44} {'大小':>7} {'无偏%':>6} {'打包%':>6}  判定")
        print("-" * 78)
        for info in results:
            name = os.path.basename(info["path"])[:42]
            ratio_s = f"{info['ratio']:.0f}" if info["ratio"] is not None else " - "
            rp_s = f"{info['ratio_packed']:.0f}" if info.get("ratio_packed") is not None else " - "
            print(f"  {name:<42} {info['size_gb']:>6.2f}G {ratio_s:>6} {rp_s:>6}  {info['type']}")
        print("=" * 78)


if __name__ == "__main__":
    main(sys.argv)
