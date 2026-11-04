# SF / MF 统一的 Direct Repair Prompt（借鉴 SRepair）

参考：SRepair — *How Far Can We Go with Practical Function-Level Program Repair?*
（[arXiv:2404.12833](https://arxiv.org/abs/2404.12833)，[代码](https://github.com/GhabiX/SRepair)）

## 从 SRepair 借鉴的四点

| SRepair 的结论/设计 | 在本模板中的落地 |
|---|---|
| zero-shot 直修已很强，few-shot 平均反而掉点 | 默认 **zero-shot**，不放 buggy/fixed 示例；示例只在需要时另加 |
| trigger test（+26.7%）、error message（+26.1%）是最有效的辅助信息 | 固定给出**完整失败测试方法 + 失败行 + 异常/堆栈**（你的原模板已有，保留并补全） |
| 双 LLM：先 CoT 生成自然语言修复建议，再据此生成补丁 | 模板 B（两阶段）：Stage-1 只出修复计划，Stage-2 带着计划出补丁 |
| 能修 multi-function bug（32 个） | 模板 A 的 MF 分支：**一次给全 N 个函数**，要求按 `FILE/FUNCTION` 头逐个返回，支持跨函数协同修改 |

与 SRepair 的差异：SRepair 用 `Fixed Function:` 让模型续写；这里面向 chat 模型，改成**显式输出格式**，便于自动解析与回填。

## 模板 A（推荐，单阶段直修；SF/MF 共用）

SF 时只渲染一个函数块，MF 时渲染 N 个函数块并切换指令与输出格式。

```text
You are a helpful assistant that fixes Java code.

# ---- SF ----
The following Java function contains a bug.

```java
{function source; 每条 buggy 行末尾加 "  // BUGGY LINE"}
```

The buggy line(s) are marked with "// BUGGY LINE". The fix may involve changes around the marked lines.

The function fails on the following test.

# ---- MF ----
The following {N} Java functions together contain a bug: a single defect whose fix spans multiple functions.

### Function 1 of {N} -- {file} -- {signature} (lines {a}-{b})
```java
{function 1 with markers}
```
...
The buggy line(s) are marked with "// BUGGY LINE". The defect can only be eliminated by coordinated changes in more than one of the functions above.

These functions fail on the following test.

# ---- 共用部分 ----
Test code:
```java
{failing test method source}
```

Failure information:
```
Test: {test_class}::{test_method}
Failure line: {失败语句}
Error message: {异常类型 + 消息}
{栈顶 3 帧}
```

Based on all the information above, please provide a correct fix for the bug.

# SF 输出
Return the complete fixed function inside a single ```java ... ``` block, and nothing else.

# MF 输出
Return the complete fixed body of EVERY function listed above, in the same order, using exactly this format and no other text:
=== FILE: <file path> ===
=== FUNCTION: <signature> ===
```java
<fixed function>
```
Do not omit, merge, rename or reorder the functions; keep every signature unchanged; do not modify the test.
```

### 几个刻意的设计

- **buggy 行标记**：`  // BUGGY LINE` 追加在行尾（不新增行，避免行号错位）；omission bug 没有"错误行"，
  在缺失位置插入 `// BUGGY LINE: missing code (omission)` 占位行。
- **MF 必须逐个返回**：跨函数修复常出现"只改一个函数"的退化解，强制返回全部 N 个函数可让模型显式决定
  哪些不变，也便于补丁回填与评测（解析失败即视为无效补丁）。
- **只给测试方法 + 失败信息，不给修复提示**：避免泄漏；函数名/文件路径保留，因为 MF 需要区分。
- **多个失败测试**：主测试给完整源码，其余只列 `类::方法`（控 token；SRepair 也只强调 trigger test）。

## 模板 B（SRepair 式两阶段，可选）

**Stage 1（repair suggestion，CoT）**：同样的 buggy code + test + failure，但末尾改为：

```text
Do NOT write the patch yet. First analyse the root cause and describe a concrete repair plan in natural language:
1) which function(s) and line(s) are wrong and why;
2) what exactly must change (conditions, values, missing logic, call order, ...);
3) if several functions must change together, how their changes must stay consistent.
Keep it under 200 words.
```

**Stage 2（patch generation）**：在同样上下文后加入：

```text
A senior engineer analysed this bug and proposed the following repair plan:
```
{stage-1 输出}
```
Apply this plan and provide the fixed code.
{与模板 A 相同的输出格式要求}
```

## 输出解析约定

- SF：取最后一个 ```java``` 代码块，去掉 `// BUGGY LINE` 注释后即为补丁函数。
- MF：按 `=== FILE: ... ===` / `=== FUNCTION: ... ===` 切分，每个块取随后的 ```java``` 代码；
  必须恰好得到 N 个函数、签名与输入一致，否则判为无效补丁。
- 生成时建议 `temperature=0.0/0.2` 各跑一次；SRepair 用 top-p 0.9、temperature 0.8、每 bug 200 采样。

## 产物

| 文件 | 说明 |
|---|---|
| `prompts_A.jsonl` | 模板 A，**816 条**（SF 553 / MF 263），含 `bug`、`class`、`n_functions`、`prompt` |
| `prompts_B_stage1.jsonl` | 模板 B 的 Stage-1，816 条 |
| `render_prompts.py` | 渲染脚本（读 `d4j_prompt_data.jsonl`） |
| `sample_prompts_A.md` | Lang-21（SF）与 Chart-2（MF，omission）渲染样例 |

> 827 个入册 bug 中，**11 个因缺关键信息被放弃**：9 个定位不到 buggy 函数、2 个测试方法不存在
> （清单见 `prompts_skipped.txt`）；其余 **816 个**生成完整 prompt。
