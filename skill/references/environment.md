# 环境依赖与协议查证参考

## Python 依赖
- Python 3.10+（开发环境为 3.13）
- `torch`（任意后端，CPU 即可）
- `safetensors`

安装：`pip install torch safetensors`

## 参考实现运行方式
```bash
# 单文件分析
python scripts/check_model.py <模型路径.safetensors>

# 目录批量扫描（分析目录下所有 .safetensors / .gguf）
python scripts/check_model.py <目录路径>

# GGUF 文件同样支持（analyze_gguf 纯 struct 解析，无需额外依赖）
python scripts/check_model.py <模型路径.gguf>
```

## 协议归属查证方法（Step 2 实操）
拿到专有 key 后，在对应代码库中 grep：

| 专有 key / format | 查证位置 | 预期归属 |
|------------------|---------|---------|
| `comfy_quant` | `comfy/ops.py` → `QUANT_ALGOS` 注册表 | ComfyUI 官方协议 |
| `format: int8_tensorwise` | `comfy/ops.py` 第 ~1094 行 | ComfyUI 官方 INT8 |
| `format: tint4_torchao` | ComfyUI-TINT4 节点 `tint4_quantizer.py` | ComfyUI 协议 + torchao 库 |
| `weight_zp` / int32 打包 | ComfyUI-TINT4 / torchao dtypes | 视上下文 |
| torchao 原生 | `torchao/dtypes/` → `tensor_impl` | torchao（不用 comfy_quant） |

## 本机开发环境（Allan 的 Windows 机器，非分享必需）
- 项目根目录: `D:/projects/model-format-verifier/`
- 权威源: `D:/projects/model-format-verifier/check_model.py`
- Python: `F:/ComfyUI-aki-v3/python/python.exe`
- 模型目录: `F:/ComfyUI-aki-v3/ComfyUI/models/diffusion_models/`
- 协议源码: `F:/ComfyUI-aki-v3/ComfyUI/comfy/ops.py`
- 注意：分享给别人时这些路径无效，应以本文件顶部通用说明为准
