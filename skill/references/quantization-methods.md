# 量化方法速查表

常用模型量化方法的思路、文件特征与 MFV 工具识别情况对照。分析陌生格式时可反查本表。

## 速查表

| 量化方法 | 思路 | 文件特征 | MFV 识别 |
|---------|------|---------|---------|
| SVDQuant (W4A4/W4A16) | SVD 低秩分解 + 残差 4bit | `svdq.quantized.weight`(int4) + `svdq.U/V/alpha` 低秩因子 + `lora_up/down` 旁路 | ⚠️ 部分（低秩提示，未升级硬判据） |
| GGUF (Q4_0/Q8_0/Q4_K...) | 块级缩放（每块共享 scale） | 文件签名 `GGUF` + GGML 类型枚举 | ✅ 完整 |
| INT8 ConvRot | QuaRot 旋转 + 通道级量化 | int8 权重 + scale + `comfy_quant.convrot` | ✅ 完整 |
| FP8 | 8-bit 浮点（e4m3/e5m2） | float8 dtype / `comfy_quant.format=fp8` | ✅ 完整 |
| NF4 (bitsandbytes) | 归一化 4bit 浮点（16 值码本） | `absmax` + `quant_map` + `bitsandbytes__nf4` | ✅ 完整 |
| Packed INT4 | 字节打包：每字节 2×4bit | int8/uint8 半宽 + scale（2D group / 1D per-row） | ✅ 完整（解包验证） |
| ComfyUI INT4 (TINT4) | int32 打包：每 int32 装 8×4bit | `comfy_quant.format=tint4_*` + int32 权重 | ✅ 完整 |
| 非对称 INT4 | zero-point 非对称 | `weight_zp` + int32 打包 | ✅ 完整 |
| torchao affine | int32 打包 8×4bit | int32 weight + scale（无 comfy_quant） | ✅ 完整 |

## 与 MFV 输出的对应

MFV 输出「量化机制」字段即本表「思路」列的翻译（`check_model.py::_quant_mechanism`）：

| 检测类型 | 输出「量化机制」 |
|---------|----------------|
| NF4 (bitsandbytes) | 归一化 4bit：16 值非线性码本 + 块级 absmax 缩放 |
| ComfyUI INT8 (convrot) | QuaRot 旋转消除离群值 → 通道级 int8 定点缩放 |
| ComfyUI INT8 | 通道级/tensorwise int8 定点缩放 |
| ComfyUI INT4 / TINT4 | int32 打包（每 int32 装 8×4bit）+ 通道级缩放 |
| ComfyUI FP8 | 8-bit 浮点（e4m3/e5m2 指数尾数分配） |
| Packed INT4 group-wise | 字节打包：每字节 2×4bit，group-wise 缩放 |
| Packed INT4 per-row | 字节打包：每字节 2×4bit，per-row 缩放 |
| torchao | affine int32 打包（每 int32 装 8×4bit） |
| 非对称 INT4 | zero-point 非对称量化（int32 打包） |
| GGUF 分块量化 | 分块量化：{主导类型} 块级缩放（块内共享 scale/dmin） |
| 接近未量化 | 无压缩（原生 fp16/bf16 精度） |
| 混合精度 | 部分层量化（量化层 + 原生精度层混合） |

## SVDQuant 识别状态（2026-08-13）

- 工具当前只做「低秩结构提示」（统计 `svdq` / `lora_` key），**不参与决定性判定**
- 升级为硬判据需真实 SVDQuant 样本验证（反例驱动原则，防虚标）
- 识别入口（待样本确认）：`svdq.quantized.weight`（int4 残差）+ `svdq.U/V`（低秩因子）+ `svdq.alpha`
