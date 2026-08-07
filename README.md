# MFV — 模型格式验证工具（Model Format Verifier）

无偏、交叉验证的模型权重量化与打包格式逆向分析工具。

**不信任文件名、不信任元数据**——只以底层物理尺寸、张量结构和解包特征为硬证据，判定模型真实的量化格式与打包布局。

## 特性

- **多格式识别**：ComfyUI INT8/INT4/FP8/NF4、Packed INT4（group-wise / per-row 字节打包）、bitsandbytes NF4、torchao int32、GGUF 分块量化（Q2_K~Q8_K / IQ 系列）、未量化（FP16/BF16/FP8）
- **证据驱动**：nibble 缺失模式 + 字节 unique 组合是打包判定的硬证据；协议归属以定义方源码注册表为准（如 ComfyUI `QUANT_ALGOS`），拒绝名称联想
- **双层次输出**：💡 普通用户速览 / 📊 性能与结构评估 / 🔍 开发者深度诊断 / 终审结论，四区模板各取所需
- **跨平台**：纯 Python + torch + safetensors，Windows / macOS / Linux 命令行直跑

## 快速开始

```bash
# 安装依赖
pip install torch safetensors

# 单文件分析（safetensors 或 GGUF）
python check_model.py <模型.safetensors>
python check_model.py <模型.gguf>

# 批量扫描目录
python check_model.py <模型目录>
```

## 输出示例

```
================================================================================
 📦 MFV 模型检查工具 v1.0.1
================================================================================
 文件: z_image_turbo_int8_convrot.safetensors
 大小: 5.78 GB
 路径: /path/to/models/

--------------------------------------------------------------------------------
 💡 【普通用户速览】
--------------------------------------------------------------------------------
  • 模型类型: ComfyUI INT8 (int8_tensorwise)
  • 典型加载: ComfyUI 原生加载 (QUANT_ALGOS 内置, 无需第三方节点)
  • 显存参考: 磁盘 5.78 GB (估算; 相比全 FP16 约省 50%)

--------------------------------------------------------------------------------
 📊 【性能与结构评估】
--------------------------------------------------------------------------------
  • 原始等效: ~11.47 GB (FP16 基础)
  • 显存节省: 约 50% ⏬ (标准全宽 INT8，无打包)
  • 量化协议: comfy_quant (format: int8_tensorwise)
  • 关键算法: QuaRot 旋转优化 (convrot_groupsize=256)

--------------------------------------------------------------------------------
 🔍 【开发者深度诊断】
--------------------------------------------------------------------------------
 [张量与数据类型分布]
  • 总 Key 数: 857
  • Key 后缀: .weight: 413 | .weight_scale: 202 | .comfy_quant: 202 | .bias: 38 | .(none): 2
  • dtype: torch.float32: 453 | torch.int8: 202 | torch.uint8: 202

 [压缩比评估 (双解读分析)]
  • 实际数据: 5.78 GB | 全 FP16 基准: ~11.47 GB
  • 双解读: 50% (无偏/全宽)  vs  25% (若4bit打包)  [差异 25pt]
  • 裁决: 50% (按最终类型 ComfyUI INT8 (int8_tensorwise) 选取)

 [量化层与采样验证]
  • 格式分布: INT8 tensorwise/per-row: 80 层
  • 解包采样: context_refiner.0.attention.out.weight_scale
      └─ 打包=False | 字节unique=255 | lo=16 hi=16
  • 示例: context_refiner.0.attention.out.weight_scale
      └─ weight 3840x3840 torch.int8, scale (3840, 1)
  • comfy_quant: {"format": "int8_tensorwise", "convrot": true, "convrot_groupsize": 256}


================================================================================
 【终审结论】
================================================================================
  [key] 含 weight_scale 伴生张量  (202 个)
  [key] 含 comfy_quant 元数据  (202 个 → ComfyUI 量化协议)
  [dtype] 存在 int8 张量  (202 个)
  [压缩] 无偏50%/打包25%  (双解读差异大)
  [结构] INT8 tensorwise  (80 层主导)
  [元数据] comfy_quant.format=int8_tensorwise
--------------------------------------------------------------------------------
  → 类型识别: ComfyUI INT8 (int8_tensorwise)
  → 识别依据: comfy_quant 专有元数据 (ComfyUI 官方量化协议, 非 torchao)
  → 典型加载: ComfyUI 原生加载 (QUANT_ALGOS 内置, 无需第三方节点)
  → 结构要点: ComfyUI 原生 int8 量化 (QUANT_ALGOS 注册格式); 带 QuaRot 旋转 (convrot_groupsize=256); 量化层 80 层主导: INT8 tensorwise/per-row
================================================================================
```

## 三种使用方式

### 1. 命令行（核心，跨平台）

```bash
python check_model.py <路径>
```

适合所有平台，脚本即工具，零安装成本。

### 2. AI Agent 技能包（Skill，跨 agent 通用）

本仓库自带 `skill/` 目录，是标准 **SKILL.md 技能包**（Anthropic Agent Skills 开放规范，Claude Code / Cursor / WorkBuddy 等主流 agent 均兼容），内含五步分析范式 + 案例库 + 参考实现。

**通用安装方式一：skills.sh CLI（推荐）**

```bash
npx skills add allanmeng/model-format-verifier@skill
```

**通用安装方式二：手动放置**

把 `skill/` 目录复制到所用 agent 的技能目录即可（不同 agent 路径不同，如 `~/.workbuddy/skills/`、`~/.claude/skills/`、`~/.cursor/skills/` 等），重启后自动识别。

**通用安装方式三：ClawHub 社区市场**

已发布到 [clawhub.ai](https://clawhub.ai) 后可一键安装。

### 3. Windows 右键菜单（零依赖，SendTo 方案）

```
scripts\install_sendto.bat     # 一键安装
```

脚本会把「Check Model」「Scan Models」快捷方式放入系统 SendTo 目录。之后右键任意 `.safetensors` / `.gguf` 文件 → **发送到 → Check Model** 即可检测；右键文件夹 → **发送到 → Scan Models** 批量扫描。

## 目录结构

```
model-format-verifier/
├── check_model.py           # 核心检测工具（权威源）
├── skill/                   # WorkBuddy 技能包
│   ├── SKILL.md             # 五步分析范式
│   ├── scripts/check_model.py
│   └── references/          # 环境参考 + 案例库
├── scripts/
│   ├── mfv_check.bat       # 右键单文件检测
│   ├── mfv_scan.bat        # 右键批量扫描
│   └── install_sendto.bat   # 右键菜单一键安装
└── docs/                    # 开发上下文 / 格式覆盖地图
```

## 核心验证逻辑

**MFV（Model Format Verifier，模型格式验证）** 的核心验证逻辑源自 Model Format Verifier Protocol（模型格式验证协议）——一套"证据无偏 + 交叉验证"的逆向分析范式，下面对它做完整展开。

### 五条核心原则

1. **证据无偏**：文件名、`w4a4_group_size`、`format` 字段都可能虚标，只以底层物理尺寸与解包特征为基准
2. **源码溯源**：协议归属查定义方注册表（`comfy_quant` → ComfyUI `QUANT_ALGOS`，**非 torchao**），拒绝名称联想
3. **严格区分力**：每个判据先问"能否排除竞争假设"，只保留硬判据，废弃无区分力特征
4. **主导降级**：主导结构（采样层 >50%）定结论；证据不足则保守降级，宁可不报不可错报
5. **闭环反馈**：以真实 Loader 运行时行为反查检测缺陷，修复保持插件无关

### 五步分析工作流

```mermaid
flowchart LR
    A["Step 1 无偏采集<br/>文件大小 · dtype · scale 形状<br/>压缩比双解读"] --> B["Step 2 源码归属<br/>专有 key → QUANT_ALGOS 查证"]
    B --> C["Step 3 交叉验证<br/>解包验证 · nibble 缺失<br/>形状自洽"]
    C --> D["Step 4 主导推导<br/>判定分支 · 保守降级"]
    D --> E["Step 5 闭环回馈<br/>Loader 反查缺陷"]
    E -.-> A
```

### 硬判据：nibble 缺失与字节 unique

4bit 打包的本质是「每字节装两个 4bit 值」，会在字节层面留下不可伪造的痕迹：

```mermaid
flowchart TD
    subgraph S1["全宽 int8"]
        A1["字节 unique ≈ 255<br/>lo = 16 全覆盖 · hi = 16 全覆盖"]
    end
    subgraph S2["真 4bit 打包"]
        B1["字节 unique = lo_u × hi_u<br/>（如 15×15 = 225）<br/>lo/hi 必有缺失（<16）"]
    end
    A1 --> Y["判定：全宽 INT8"]
    B1 --> Z["判定：Packed INT4"]
    A1 -. "解包值域 ∈[-8,7] 无区分力 ❌" .-> X["任何字节拆 nibble 都成立"]
    B1 -. "解包值域 ∈[-8,7] 无区分力 ❌" .-> X
```

### 判定决策树

```mermaid
flowchart TD
    M["模型张量分析"] --> N1{"bitsandbytes__nf4<br/>quant_state 标记?"}
    N1 -- 是 --> R1["NF4 (bitsandbytes)"]
    N1 -- 否 --> N2{"comfy_quant 元数据?"}
    N2 -- 是 --> N3{"format 关键词?"}
    N3 -- int8 --> R2["ComfyUI INT8"]
    N3 -- int4/tint4 --> R3["ComfyUI INT4"]
    N3 -- fp8 --> R4["ComfyUI FP8"]
    N3 -- nf4 --> R5["NF4 (ComfyUI)"]
    N2 -- 否 --> N4{"int8/uint8 权重<br/>+ 伴生 scale?"}
    N4 -- "解包=4bit + 2D scale" --> R6["Packed INT4 group-wise"]
    N4 -- "解包=4bit + 1D scale" --> R7["Packed INT4 per-row"]
    N4 -- "全宽 + gs 虚标" --> R8["伪 W4A4 (Group-wise INT8)"]
    N4 -- "全宽 + 无虚标" --> R9["INT8 全宽"]
    N4 -- "无伴生 + 压缩比高" --> R10["接近未量化"]
```

### 三类格式的检测路径

| 格式家族 | 识别入口 | 关键判据 | 特殊处理 |
|---------|---------|---------|---------|
| safetensors 量化 | `weight_scale` / `comfy_quant` 等后缀 | 解包验证 + 形状自洽 | nibble 硬判据 |
| bitsandbytes NF4 | `absmax` + `quant_map` + `bitsandbytes__nf4` | quant_state 标记（决定性） | 不走解包验证（码本索引体系） |
| GGUF 分块量化 | 文件签名 `GGUF` | 类型分布按权重元素占比 | 纯 struct 解析，不读数据区 |

- 保守原则：证据不足时输出"疑似/待确认"，宁可不报不可错报

## 案例库

| 模型 | 表象 | 真实结论 | 暴露的漏洞 |
|------|------|---------|-----------|
| kr2fp_wa4 | 文件名 wa4 | Packed INT4 | 死分支误判 |
| krea2_turbo-int8_convrot | 文件名 int8 | INT4 per-row 打包 | 文件名不可信 |
| z_image_turbo_int8_convrot | comfy_quant | ComfyUI INT8 | 归属误判 torchao |
| z_image_turbo_nf4_v2 | 文件名 nf4 | NF4 (bitsandbytes) | 漏判 bnb 体系 |

详见 `skill/references/case-studies.md`。

## 许可证

MIT

## 贡献

- 权威源：根目录 `check_model.py`（唯一升级对象）
- 新格式支持遵循"反例驱动"：遇到误判/漏判 → 提供模型特征 → 补充判据 → 沉淀案例
