# MFV 真实案例库

每个案例记录了：表象（文件名/元数据暗示）、真实结论、判据、暴露的检测漏洞。
用于：新模型判定时对照；检测逻辑回归测试。

## 案例 1：kr2fp_wa4 — 真 Packed INT4（曾被误判伪 W4A4）
- **表象**：文件名含 `wa4`，`w4a4_group_size=64`，scale 2D `[96, 6144]`
- **真实结论**：Packed INT4（group-wise, nunchaku 风格）
- **判据**：`G×gs = 96×64 = 6144 = 2×weight宽(3072)` 打包解读闭合；解包验证 nibble 缺失（字节 unique=225=15×15, lo=15/hi=15）；`prepare_onednn_weights` 转换成功
- **暴露的漏洞**：`w.shape[1]*2 == K` 恒 False 死分支 → Packed INT4 永远判不中 → 误落"伪 W4A4"分支

## 案例 2：krea2_turbo-int8_convrot — 真 INT4 per-row（文件名误导）
- **表象**：文件名含 `int8`，scale 1D per-row，无 group_size
- **真实结论**：INT4 per-row 打包（非 nunchaku 标准）
- **判据**：解包验证 nibble 缺失（字节 unique=216, lo=15/hi=15）；1D scale 也能打包
- **暴露的漏洞**：文件名不可信；检测逻辑只看 2D scale 判打包，漏掉 1D per-row 打包场景

## 案例 3：z_image_turbo_int8_convrot — ComfyUI INT8（归属误判）
- **表象**：`comfy_quant.format=int8_tensorwise`，scale `(3840,1)` 列向量
- **真实结论**：ComfyUI INT8 (int8_tensorwise)，带 QuaRot 旋转
- **判据**：format 字段（查 `comfy/ops.py` QUANT_ALGOS 确认归属）；解包验证 nibble 全覆盖（字节 unique=255, lo=16/hi=16）= 全宽
- **暴露的漏洞**：① comfy_quant 带 "quant" 被误归 torchao（实际是 ComfyUI 官方协议）；② 解包值域判据无区分力（任何字节拆 nibble 都 ∈[-8,7]）

## 案例 4：Flux.2-Klein / Krea2-Turbo tint4_torchao — ComfyUI INT4
- **表象**：`comfy_quant.format=tint4_torchao`，weight int32 打包 + weight_zp
- **真实结论**：ComfyUI INT4（TINT4 量化器产出，torchao 库量化 + comfy_quant 协议保存）
- **判据**：format 字段；int32 打包（每 int32 装 8 个 4bit → fp16 参考 ×8）
- **暴露的漏洞**：压缩比未按 int32 打包位宽修正 → 算出 167% 荒谬值（修复后 29%/33% 正确）

## 案例 5：ZIT_REDZimageTurbo2.0-INT8-Convrot — ComfyUI INT8（同族验证）
- **表象**：`comfy_quant.format=int8_tensorwise` + convrot，scale `(3840,1)`
- **真实结论**：ComfyUI INT8，与 z_image_turbo 同族（量化结构相同，未量化层 dtype 不同：fp32 vs bf16）
- **判据**：同案例 3；两个独立模型交叉验证判定分支稳定性

## 案例 6：z_image_turbo_nf4_v2 — bitsandbytes NF4（漏判修复）
- **表象**：文件名含 nf4；`.absmax`/`.quant_map`/`.bitsandbytes__nf4` 后缀各 170 个
- **真实结论**：NF4 (bitsandbytes) 量化（170 层 NF4 + 243 层未量化，部分量化）
- **判据**：`bitsandbytes__nf4` quant_state 标记（决定性物理证据）；NF4 码本 16 值非线性（-1.0~1.0）；压缩比打包解读 ×4
- **暴露的漏洞**：脚本只认 weight_scale 体系 → 不认识 absmax/quant_map 码本体系 → 误判"混合精度"

## 案例 7：qwen_3_4b_fp4_flux2 — 文件名 fp4，实际 FP8（float8 识别漏洞）
- **表象**：文件名声称 `fp4`（4bit 浮点）；`comfy_quant.format=float8_e4m3fn`（242 个）
- **真实结论**：ComfyUI FP8 (float8_e4m3fn)——**声称 4bit、实际 8bit，差一倍位宽**
- **判据**：comfy_quant.format 决定性（float8_e4m3fn → FP8 分支）
- **暴露的漏洞**：格式判断只认 `fp8` 不认 `float8` → float8_e4m3fn 掉进"ComfyUI 量化"兜底、量化机制/W/A 全"待定" → 5 处判断统一补 `"float8" in` 修复
- **教训**：格式名变体（fp8/float8_e4m3fn）不能靠子串联想，协议注册表里出现的每个格式名都要覆盖

## 案例 8：Huihui-Qwen3-VL-4B-int8_mixed_convrot — 文件名 int8，实际 INT4 per-row（同族骗局）
- **表象**：文件名声称 `int8_mixed_convrot`
- **真实结论**：INT4 per-row 打包（无 group_size）——与案例 2（krea2_turbo-int8_convrot）同款"文件名 int8 实为 INT4"骗局
- **判据**：解包验证=合法 4bit + 1D per-row scale（无 2D group scale）
- **教训**：`int8` 出现在文件名里既不表示存储位宽也不表示加载精度，唯一可信的是解包证据

## GGUF 检测说明（v1.0.0 新增）
- GGUF 是自包含二进制容器（文件签名 "GGUF"），非 safetensors；`analyze_gguf` 纯 struct 解析，只扫张量信息表不读数据区
- 分块量化（Q4_K/Q6_K 等）scale 内嵌数据块，不走 nibble/解包判据；主导类型按权重元素占比判定（非张量个数）
- 张量类型枚举 31 种（F16/F32/BF16/Q2_K~Q8_K/IQ 系），位宽近似表校准自 llama.cpp

## 判据区分力对照表（Step 3 核心）

| 判据 | 全宽 int8 表现 | 真打包 4bit 表现 | 区分力 |
|------|--------------|-----------------|--------|
| 解包值域 ∈[-8,7] | 也成立（无意义） | 成立 | ❌ 无 |
| lo/hi 直方图对称 | 对称 | 对称 | ❌ 无 |
| 字节 unique | ≈255（全覆盖） | =lo_u×hi_u（如 225） | ✅ 强 |
| nibble 缺失（lo/hi<16） | 16/16 全覆盖 | 必有缺失（如 15/15） | ✅ 强 |
| scale 形状自洽 G×gs vs K | ==weight宽 | ==2×weight宽 | ✅ 辅助 |
| 压缩比双解读差异 | 大（未量化层多）| 大（打包层多）| ⚠ 需结合解包 |
