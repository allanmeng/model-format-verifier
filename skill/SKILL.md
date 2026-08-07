---
name: model-format-verifier-protocol
description: "模型权重量化与打包格式的无偏逆向分析范式 (MFV)。用于判断 safetensors 模型的量化格式（TorchAO/ComfyUI INT4-INT8/NF4/Packed INT4/nunchaku/GPTQ/AWQ/FP8/GGUF 等）：元数据归属查证、解包验证、nibble 缺失判据、主导格式降级、Loader 反查漏洞。触发场景：模型检测、check_model、量化格式识别、判断模型能否用某加载器、逆向分析模型结构、w4a4/int8/int4/tint4/tensorwise/convrot 等格式分析。"
---

# Model Format Verifier Protocol (MFV)

无偏、交叉验证的模型权重量化与打包格式逆向分析范式。

## 包结构（自包含，可直接分享）

```
model-format-verifier-protocol/
├── SKILL.md                  # 本文件：五步分析范式（核心）
├── scripts/
│   └── check_model.py        # 参考实现（可执行检测工具）
└── references/
    ├── environment.md        # 环境依赖 + 协议查证方法
    └── case-studies.md       # 真实案例库 + 判据区分力对照表
```

运行参考实现：
```bash
pip install torch safetensors
python scripts/check_model.py <模型.safetensors>        # 单文件
python scripts/check_model.py <目录>                    # 批量扫描
```

## Core Principles (核心法则)

1. **证据无偏**：不信任元数据/虚标名称（文件名、`w4a4_group_size`、`format` 字段都可能虚标），只以底层物理尺寸与解包特征为基准。
2. **源码溯源**：协议归属以定义方注册表为准（如 ComfyUI `comfy/ops.py` 的 `QUANT_ALGOS`），拒绝名称联想（`comfy_quant` 带 quant ≠ torchao）。
3. **严格区分力**：每个判据先问"能否排除竞争假设"，剔除低区分力特征（如 lo/hi 直方图对称性——全宽与打包都成立），仅保留硬判据（如 nibble 缺失模式、字节 unique 组合）。
4. **主导降级**：主导结构（采样层 >50%）决定结论；非主导/混淆结构输出 Warning；不确定则保守降级为"疑似/待确认"，宁可不报不可错报。
5. **闭环反馈**：以真实 Engine/Loader（如 wa4/oneDNN/ComfyUI ops）的运行时行为反查检测代码缺陷，修复保持插件无关。

## 5-Step Analysis Workflow

### Step 1: Read-Only Evidence Collection (无偏采集)
- [ ] 物理数据：File Size, Raw Tensor Shapes, dtypes, Scale/Zero-Point Shapes。
- [ ] 关键 key 后缀统计：`weight`, `weight_scale`, `weight_zp`, `comfy_quant`, `w4a4_group_size`, `weight_b0/b1`, `weight_sh0/sh1`。
- [ ] dtype 统计：**排除标量元数据**（shape 为 `(1,)` 或 numel≤4 的，如 `w4a4_group_size`），避免误报。
- [ ] 原始压缩比计算（双解读）：
  ```
  Unbiased_Ratio = Total_Bytes / (Σ Params × FP16_Bytes)          # int8 按全宽算, 不信任元数据
  Packed_Ratio   = Unbiased 但 int8/uint8 伴生 scale 的层 ×2       # 若 4bit 打包则每字节 2 值
  ```
  *禁止在 Step 1 注入 4bit/8bit 假设，防止虚标元数据污染 Baseline。双解读差异 >5pt 提示存在打包层，但最终由 Step 3 解包验证裁决。*

### Step 2: Protocol Origin Verification (源码归属)
- [ ] 抓取专有 Key（`comfy_quant`, `weight_zp`, `torchao` 特征, `__w4a4_quarot__` 等）。
- [ ] 查找对应库注册表 / Op 定义确认 Owner：
  - `comfy_quant` → grep `ComfyUI/comfy/ops.py` 的 `QUANT_ALGOS`（ComfyUI 官方协议，非 torchao！）
  - `format: tint4_torchao` → TINT4 量化器用 torchao 库但走 comfy_quant 协议
  - `format: int8_tensorwise` → ComfyUI 官方注册格式，带 convrot 参数
  - torchao 原生 → 查 `torchao/dtypes/` 的 `tensor_impl`（有自己的数据结构，不用 comfy_quant）
- [ ] 归属结论必须来自代码查证，禁止凭名字猜。

### Step 3: Cross-Validation & Power Checking (区分力检验)
- [ ] 检验判据区分力：自问"此特征能否百分百排除竞争假设？"
  - ❌ 废弃项：Lo/Hi nibble 值域范围分布、直方图对称性（全宽 int8 与打包 int4 可能重合——krea2 对照实验证明）。
  - ❌ 废弃项：解包值域 ∈[-8,7] 且 ≤16 值（任何字节拆两个 nibble 天然满足，无区分力）。
  - ✅ 保留项：**Nibble 缺失模式 + 字节 unique 组合**：
    ```python
    byte_u = unique(flat)            # 全宽 int8 ≈255 (接近256全覆盖)
    lo_u   = unique(flat & 0x0F)     # 全宽 =16 (全覆盖)
    hi_u   = unique((flat>>4)&0x0F)  # 全宽 =16
    # 真 4bit 打包: nibble 必有缺失 (lo_u<16 或 hi_u<16)
    #             且 byte_u ≈ lo_u × hi_u (如 15×15=225, 16×16 不全)
    packed = (lo_u < 16 or hi_u < 16) and byte_u <= lo_u*hi_u and byte_u >= lo_u*hi_u*0.8
    ```
- [ ] 解包采样验证（Unpack Verification）：提取代表性层（int8/uint8 + 伴生 scale），检查上述字节/nibble 特征。注意 1D per-row scale 也可能是打包（如 krea2），不能只看 2D scale。
- [ ] 形状自洽验证：scale 2D `[G,N]` 时，`K_full = G × gs`；若 `K_full == 2×weight宽` → 打包解读闭合；若 `K_full == weight宽` → 全宽解读。scale 形如 `(N,1)/(1,N)` 是 tensorwise/per-row 列向量，不走 group-wise 推断。

### Step 4: Evidence-Driven Deduction (主导推导与降级)
- [ ] 判定权重：决定性物理证据（comfy_quant format / 解包验证）> 主导层格式（>50% count）> 辅助元数据。
- [ ] 逻辑分支检查：禁用 `A == B` 类型的静态死分支（曾因 `w.shape[1]*2 == K` 恒 False 导致 Packed INT4 永远判不中），改用区间与比例容差。
- [ ] 冲突处理：若存虚标/伪装元数据（如假 W4A4：gs 元数据与 scale 反推不符），输出【主导格式 + 混淆警告】而非静默选择。
- [ ] comfy_quant 按 format 细分（归属 ComfyUI 协议）：
  - `int8*` → ComfyUI INT8（tensorwise/group-wise）
  - `int4/tint4` → ComfyUI INT4（int32 打包）
  - `fp8*` → FP8；`nf4*` → NF4；其他 → 原样输出
- [ ] 证据不足时输出"疑似 Packed INT4（证据不足）"或"格式不明确"，不做武断判定。

### Step 5: Loader Exploitation & Loopback (漏洞回馈)
- [ ] 逆向测试：将判定模型送入对应 Target Loader（如 `wa4_loader` 的 `prepare_onednn_weights`、ComfyUI 原生 `ops.py` 加载路径），确认转换/加载是否成功。
- [ ] 崩溃/成功反证：Loader 报错 → 反查 Step 3 交叉验证逻辑；Loader 成功 → 反证判定（kr2fp 案例：`prepare_onednn_weights` 成功确认真打包）。
- [ ] 每次发现检测逻辑缺陷，修复后保持**插件无关**（工具只对模型下结论，不为特定插件输出可用性判断）。

## 判定分支速查表

| format 特征 | 判定 | 关键判据 |
|------------|------|---------|
| `comfy_quant.format=int8_tensorwise` | ComfyUI INT8 | format 字段 + 字节 unique≈255/nibble=16 |
| `comfy_quant.format=tint4_torchao` | ComfyUI INT4 | format 字段 + int32 打包 + weight_zp |
| `bitsandbytes__nf4` 标记 | NF4 (bitsandbytes) | quant_state 标记 + absmax/quant_map 码本 |
| GGUF 文件签名 `GGUF` | GGUF 分块量化 | 主导类型按权重元素占比 (Q4_K/Q6_K/IQ 系) |
| 2D scale + 解包验证通过 | Packed INT4 group-wise | G×gs=2K + nibble 缺失 + 无 NaN |
| 1D scale + 解包验证通过 | Packed INT4 per-row | 无 group_size, 每行一 scale |
| 全宽 int8 + gs 元数据不符 | 伪 W4A4 (Group-wise INT8) | scale 反推 gs ≠ 元数据 |
| 无 weight_scale + 压缩比显著 | INT8/混合精度 | 无伴生结构 |
| 压缩比 ≥75% 无量化结构 | 接近未量化 | — |

## 真实案例库 (详见 references/case-studies.md)

| 模型 | 表象 | 真实结论 | 发现的漏洞 |
|------|------|---------|-----------|
| kr2fp_wa4 | 文件名 wa4, gs=64 | Packed INT4 (真 nunchaku) | 死分支 `w.shape[1]*2==K` 恒 False → 误判伪 W4A4 |
| krea2_turbo-int8_convrot | 文件名 int8 | INT4 per-row 打包 | 文件名不可信; 1D scale 也可能是打包 |
| z_image_turbo_int8_convrot | comfy_quant | ComfyUI INT8 (非 torchao) | comfy_quant 归属误判 torchao; 解包判据无区分力 |
| Flux/Krea2 tint4_torchao | comfy_quant | ComfyUI INT4 | 压缩比 167% 错算 (int32 打包未按 ×8 修正) |
| z_image_turbo_nf4_v2 | 文件名 nf4 | NF4 (bitsandbytes) | 只认 weight_scale 体系 → 漏判 bnb (absmax/quant_map) |

## 环境与工具
- 依赖: Python 3.10+, torch, safetensors（详见 references/environment.md）
- 参考实现: `scripts/check_model.py`（本包内自带）
- 协议查证: 见 references/environment.md 的 QUANT_ALGOS 查证表
