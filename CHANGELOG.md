# Changelog

本项目所有值得记录的变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

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
