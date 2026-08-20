# Changelog

本项目所有值得记录的变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-08-20

### Added
- 📋 **【文件名审计】板块**（🔍 诊断之后、终审之前）：原始文件名 → 逐段核验 → 标准化命名
  - 架构核验：GGUF 精确（general.architecture）/ safetensors key 家族推断 + 架构别名归一化（t5encoder/umt5→T5 等）
  - 参数量核验：文件名 B 数 vs fp16 等效反推（15% 容差；声称 >1.3× 提示"可能为单塔/裁剪"）
  - 量化/精度核验：文件名声称 vs 文件证据（✓ 一致 / ✗ 声称不符），缺失自动补全
  - 标准化命名：身份段原样保留（含作者前缀）+ 声称不符段替换为验证值 + 缺失段补全
- 📊 性能区新增**「激活位宽 (A) 推导」块**：模型元数据 → 文件后缀 → 权重位宽 W → 激活位宽 A（候选参考，⚠ 引擎相关非文件证据）

### Fixed
- `float8_e4m3fn` 等格式未识别：判定只认 `fp8` 不认 `float8` → 类型掉兜底、量化机制/W/A 全"待定"（5 处判断补全）
- 文件名审计参数量对 int32 打包层（weight_b0/b1）低估 8 倍 → 改用 fp16 等效基准反推（与压缩比同口径）

### Changed
- case-studies 新增案例 7（`qwen_3_4b_fp4_flux2`：文件名 fp4、实际 FP8）、案例 8（`Huihui-int8_mixed`：文件名 int8、实际 INT4 per-row）
- 架构词表新增 MiniMax（文件名 + key 模式 + 别名归一化）

## [1.0.1] - 2026-08-08

### Fixed
- 未量化模型判定：显存节省说明误归"部分量化" → 修正为"无压缩，FP16/BF16 原生"
- 未量化模型"量化协议"重复显示类型名 → 修正为"无量化（原生 FP16/BF16）"
- 未量化类型文案固定"bf16/fp16 为主" → 动态读取实际主 dtype（fp16 / bf16）
- ⏬ 图标仅在显存节省 >0.5% 时显示，并消除 `-0%` 浮点噪声

### Changed
- 品牌缩写统一为 **MFV**（Model Format Verifier），MFVP 字样全部退役（skill 目录 slug 除外）

## [1.0.0] - 2026-08-07

### Added
- 输出重构为四区模板：💡 普通用户速览 / 📊 性能与结构评估 / 🔍 开发者深度诊断 / 终审结论，含版本横幅与完整路径显示
- 压缩比裁决逻辑：终审后按最终类型自动选取无偏/打包解读
- **GGUF 检测**：文件签名识别 + 版本解析 + 31 种 GGML 张量类型分布 + 压缩比估算（纯 struct，不读数据区）
- **bitsandbytes NF4 识别**：`absmax` / `quant_map` / `bitsandbytes__nf4` 码本体系
- Windows 右键集成（Nilesoft + SendTo 方案）与一键同步脚本

### Changed
- 术语「魔数」→「文件签名」
- 支持范围明确为 `.safetensors` + `.gguf` 两类

### Fixed
- bnb NF4 漏判：只认 weight_scale 体系导致 NF4 模型误判"混合精度"
- GGUF 主导判定按权重元素数（张量个数会被小张量干扰）
- 环境噪声：吞掉 pynvml FutureWarning（过滤需在 import torch 之前）
