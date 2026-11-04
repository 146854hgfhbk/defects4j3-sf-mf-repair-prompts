# Defects4J 3.0.1 — SF/MF Function-Level Repair Prompts

面向 **Defects4J 3.0.1** 的 direct-repair prompt 数据集：**816 个 bug**（单函数 SF **553**、跨函数 MF **263**），
每个 prompt 含构造 LLM 修复所需的四项信息：

1. **失败测试方法**（完整 `@Test` 源码）
2. **失败测试行**（具体语句）+ 异常/堆栈
3. **Buggy Code**（完整 Java 方法；MF 为多个方法）
4. **Buggy Line(s)**（方法内出错行，行尾标 `// BUGGY LINE`）

## 目录

```
prompts/
  prompts_A.jsonl          # 主数据：bug, class, n_functions, prompt   （816 条）
  prompts_B_stage1.jsonl   # SRepair 式两阶段：Stage-1（根因分析与修复计划）
  d4j_prompts.jsonl        # 精简版 bug, class, prompt
  prompts_skipped.txt      # 因缺关键信息放弃的 11 个 bug
docs/
  prompt_templates.md      # 模板设计（SF/MF 统一、SRepair 借鉴点、输出解析约定）
  sample_prompts_A.md      # SF / MF / omission 渲染样例
  extraction.md            # 四项数据的抽取方法与已知坑
data/
  d4j_prompt_data.jsonl    # 底层结构化数据（827 条，含函数源码、失败行、堆栈）
scripts/
  d4j_prompt_extract.py    # 抽取脚本（D4J 元数据 + VCS diff + 测试源码）
  render_prompts.py        # 渲染脚本（生成 prompts_A / B_stage1）
granularity/
  d4j3_granularity_final.csv   # 854 个 bug 的 SF/MF/NAF 判定
  per_project_final.csv        # 按项目分布
  summary_final.csv            # 汇总
  transition_matrix.csv        # 旧口径 -> 新口径迁移
```

## Prompt 设计要点（借鉴 SRepair）

SRepair（[arXiv:2404.12833](https://arxiv.org/abs/2404.12833)，[code](https://github.com/GhabiX/SRepair)）的实证结论：

- zero-shot 直修已很强，few-shot 平均反而掉点 -> 本数据集默认 **zero-shot**，不放示例
- **trigger test（+26.7%）与 error message（+26.1%）是最有效的辅助信息** -> 固定给出完整失败测试 + 失败行 + 异常
- 双 LLM：先 CoT 修复建议再生成补丁 -> 见 `prompts_B_stage1.jsonl`
- 可修 multi-function bug -> MF 分支一次给全 N 个函数，按 FILE/FUNCTION 头逐个返回

**SF**：输出单个 java 代码块。
**MF**：输出 N 个函数，格式：

```
=== FILE: <file path> ===
=== FUNCTION: <signature> ===
```java
<fixed function>
```
```

## 数据质量

| 项 | 值 |
|---|---:|
| 可用 prompt | 816 |
| 其中 SF / MF | 553 / 263 |
| 放弃 | 11（缺 buggy 函数 9 个、缺测试方法 2 个） |
| 失败测试总数 | 1739 |
| prompt 中位长度 | ~3.4k 字符 |

放弃清单：`Closure-55, Closure-78, Closure-145, Codec-12, JacksonDatabind-21, JacksonDatabind-37, Lang-23, Math-20, Math-63, Time-11, Time-22`

## 复现

```bash
# 依赖本地 Defects4J 3.0.1 安装（framework/ 与 project_repos/）
python scripts/d4j_prompt_extract.py        # 产出 data/d4j_prompt_data.jsonl
python scripts/render_prompts.py            # 产出 prompts/*
```

## 说明

- bug 与源码来自 [Defects4J](https://github.com/rjust/defects4j) 3.0.1；代码片段版权归各上游项目所有。
- 本仓库只包含 **派生元数据与 prompt**，不包含 Defects4J 的完整源码树。
- 若用于论文，请同时引用 Defects4J 与 SRepair。
