# SF / MF Direct Repair Prompt

面向 Defects4J 3.0.1 的单阶段直修（direct repair）提示词，SF 与 MF 共用一套骨架、只在
「函数块数量 / 指令措辞 / 输出格式」三处分支。

参考：SRepair — *How Far Can We Go with Practical Function-Level Program Repair?*
（[arXiv:2404.12833](https://arxiv.org/abs/2404.12833)，[代码](https://github.com/GhabiX/SRepair)）

---

## 1. SF 模板（N = 1）

```text
You are a helpful assistant that fixes Java code.

The following Java function contains a bug.

```java
{function source; 每条 buggy 行末尾加 "  // BUGGY LINE"}
```

The buggy line(s) are marked with "// BUGGY LINE". The fix may involve changes around the marked lines.

The function fails on the following test.
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

Based on all the information above, please provide a correct fix for the bug.Return the complete fixed function inside a single ```java ... ``` block, and nothing else.
```

## 2. MF 模板（N >= 2）

```text
You are a helpful assistant that fixes Java code.

The following {N} Java functions together contain a bug: a single defect whose fix spans multiple functions.

### Function 1 of {N} -- {file} -- {signature} (lines {a}-{b})

```java
{function 1 with markers}
```

### Function 2 of {N} -- {file} -- {signature} (lines {c}-{d})

```java
{function 2 with markers}
```

The buggy line(s) are marked with "// BUGGY LINE". The defect can only be eliminated by coordinated changes in more than one of the functions above.

These functions fail on the following test.

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

Based on all the information above, please provide a correct fix for the bug.Return the complete fixed body of EVERY function listed above, in the same order, using exactly this format and no other text:
=== FILE: <file path> ===
=== FUNCTION: <signature> ===

```java
<fixed function>
```

Do not omit, merge, rename or reorder the functions; keep every signature unchanged.
```

---

## 3. 渲染规则

| 项 | 规则 |
|---|---|
| 函数来源 | 开发者补丁触及的函数（perfect function-level FL）；`in_function=true` 且抽到源码 |
| buggy 行标记 | 行尾追加 `  // BUGGY LINE`，**不新增行**，避免行号错位 |
| omission bug | 缺失位置插入 `// BUGGY LINE: missing code (omission)` 占位行 |
| 行号 | `start` / `end` 为 1-based 闭区间，覆盖整个函数（含签名与右花括号） |
| 失败信息 | 只给主失败测试：完整方法源码 + 失败语句 + 异常 + 栈顶 3 帧 |
| 其他失败测试 | 只列 `类::方法`，不展开源码（控 token） |
| 不做的事 | 不给修复提示、不给参考补丁、不给 few-shot 示例（zero-shot） |

## 4. 为什么这样设计（源自 SRepair 的实证）

| SRepair 的结论 | 本模板的落地 |
|---|---|
| zero-shot 直修已很强，few-shot 平均反而掉点 | 默认 zero-shot，不展示 buggy/fixed 示例 |
| trigger test（+26.7%）、error message（+26.1%）是最有效的辅助信息 | 固定给出完整失败测试 + 失败行 + 异常/堆栈 |
| 双 LLM：先出自然语言修复建议，再生成补丁 | 模板 B（两阶段，可选） |
| 能修 multi-function bug | 模板 A 的 MF 分支：一次给全 N 个函数，按 `FILE/FUNCTION` 头逐个返回 |

与 SRepair 的差异：SRepair 用 `Fixed Function:` 让模型续写；这里面向 chat 模型，
改为**显式输出格式**，便于自动解析与回填。

## 5. 输出解析约定

- **SF**：取回复里的 ```java 代码块（模型常只给一个），去掉 `// BUGGY LINE` 注释后即为修复函数。
- **MF**：按 `=== FILE: ... ===` / `=== FUNCTION: ... ===` 切分，每块取随后的 ```java 代码；
  必须恰好得到 N 个函数、签名与输入一致，否则判为无效补丁。
- 解析失败不"猜"，直接记为无效样本。

## 6. 模板 B（两阶段，可选）

**Stage 1（repair plan）**：同样的 buggy code + test + failure，末尾改为：

```text
Do NOT write the patch yet. First analyse the root cause and describe a concrete repair plan in natural language.
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

## 7. 产物

| 文件 | 说明 |
|---|---|
| `prompts/prompts_A.jsonl` | 模板 A，**816 条**（SF 553 / MF 263），字段：`bug`、`class`、`n_functions`、`missing`、`prompt` |
| `prompts/prompts_B_stage1.jsonl` | 模板 B 的 Stage-1，816 条 |
| `prompts/d4j_prompts.jsonl` | 精简版：`bug`、`class`、`prompt` |
| `prompts/prompts_skipped.txt` | 因缺关键信息未入册的 11 个 bug |
| `scripts/render_prompts.py` | 渲染脚本（读 `data/d4j_prompt_data.jsonl`） |
| `docs/sample_prompts_A.md` | SF（Lang-21）与 MF（Chart-2）的渲染样例 |

> 827 个入册 bug 中，**11 个因缺关键信息被放弃**：9 个定位不到 buggy 函数、2 个测试方法不存在。
> 其余 **816 个**生成完整 prompt。
