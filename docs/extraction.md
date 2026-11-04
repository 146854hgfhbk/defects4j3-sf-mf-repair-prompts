# Defects4J 3.0.1 Prompt 数据（827 个入册 bug）

为每个 bug 抽取构造 LLM 修复 Prompt 所需的四项数据：

| 字段 | 内容 | 来源 |
|---|---|---|
| **failing test method** | 触发 bug 的完整 `@Test` 方法源码 | `trigger_tests/<bug>` 给出 `类::方法`；测试源码取 **fixed 版本**（即 D4J 检出后的测试状态） |
| **failing test line** | 测试方法中真正失败的那一行（含完整语句）+ 异常/堆栈 | 先按堆栈帧行号，再用异常消息（`expected:<35> but was:<34>`、`LANG-677` 等）在方法体内反查校正 |
| **buggy code** | 含 bug 的完整 Java 方法源码（MF 会有多个方法） | 由 **buggy↔fixed 的 VCS diff** 得到改动行，再映射到 **buggy 版本**里的方法 |
| **buggy line(s)** | 方法内具体出错的行（或 omission 的缺失位置） | 同上，取 diff 的 `-` 侧行号；纯新增则是缺失点，标记 `code: null` |

## 关键坑与处理（都是实测出来的）

1. **`framework/projects/<P>/patches/<bid>.src.patch` 是反向补丁**
   （fixed → buggy）：`+` 行才是 buggy 代码，`+start` 才是 buggy 行号。
2. **补丁里的 hunk 行号不可靠**：`Chart-10` 的补丁写 `+62`，但 buggy 文件只有 60 行，
   且同一语句在 buggy 里是一行、在补丁里被拆成两行。→ 改用 **VCS diff**
   （`git diff buggy fixed` / `svn diff -r buggy:fixed`）定位真实改动行。
3. **原始 diff 有噪声**：两个 revision 之间常夹带 license 头年份、ChangeLog。
   → 用框架里的 `.src.patch` 做**指纹过滤**，只保留与官方修复代码片段匹配的 hunk。
4. **堆栈行号会漂移**：`Chart-8` 堆栈写 `WeekTests.java:530`，实际断言在 526。
   → 用异常消息 `expected:<35> but was:<34>` 在方法体内反查，命中率见下方统计。
5. **`@Test` 方法签名可能跨行**（`public void testX()\n throws ...\n {`）：
   解析器已修正为用签名首行作为方法起点。
6. **测试方法可能定义在父类**：沿 `extends` 链回溯一层取方法。
7. **Omission bug**（原代码漏写逻辑）：diff 只有 `+` 行，buggy 行是"缺失位置"，
   输出 `code: null` + `note: "missing code (omission)"`，并保留 `missing_fixed_code` 便于核对。
8. **测试源码取 fixed 版本**：D4J 的 test patch 使 checkout 测试文件与 fixed 版本一致
   （Lang-21 第 237 行实测吻合）。

## 流程

```text
framework/projects/<P>/trigger_tests/<bid>     -> 测试类::方法 + 异常 + 堆栈
framework/projects/<P>/patches/<bid>.src.patch -> 修复代码指纹（过滤噪声）
git/svn diff <buggy_rev> <fixed_rev>           -> 真实改动（文件/hunk/行号）
<buggy_rev>:<file>                             -> buggy 方法（AST/括号解析）
<fixed_rev>:<TestClass>.java                   -> 测试方法 + 失败行
```

脚本：`d4j_prompt_extract.py`（服务器 `/home/yifeipei/d4j_prompt_extract4.py`），
对 827 个 in-scope bug 约 10 分钟跑完。

## 产物

| 文件 | 说明 |
|---|---|
| `d4j_prompt_data.jsonl` | 827 条结构化记录（含 `prompt` 字段） |
| `d4j_prompts.jsonl` | `{"bug": ..., "prompt": ...}`，直接喂模型 |
| `sample_prompts.md` | Lang-21、Chart-2 两个渲染样例 |
| `d4j_prompt_extract.py` | 提取脚本 |

## 覆盖情况（827 个）

- 有 buggy 方法：**818**；有失败测试：**825**；两者齐全：**816（98.7%）**
- 失败测试共 1739 个；失败行定位来源：`trace` 1028、`trace_only` 331、`message` 166、`first_assert` 111、`trace_shift` 103
- 已知缺口：**9** 个未定位到 buggy 方法、**2** 个测试方法在类及父类中不存在；
  这 11 个 bug 在 prompt 层直接放弃（`prompts_skipped.txt`），不进入修复实验。

## 记录 schema

```json
{
  "bug": "Lang-21",
  "project": "Lang",
  "bug_id": "21",
  "class": "SF",
  "subtype": "SH",
  "buggy_revision": "a655a3f...",
  "fixed_revision": "3e5d5dc...",
  "buggy_functions": [
    {
      "file": "src/main/java/org/apache/commons/lang3/time/DateUtils.java",
      "name": "isSameLocalTime",
      "sig": "public static boolean isSameLocalTime(Calendar cal1, Calendar cal2)",
      "start": 258, "end": 270,
      "source": "...完整方法源码...",
      "buggy_lines": [{"line": 265, "code": "cal1.get(Calendar.HOUR) == cal2.get(Calendar.HOUR) &&"}],
      "in_function": true
    }
  ],
  "failing_tests": [
    {
      "class": "org.apache.commons.lang3.time.DateUtilsTest",
      "method": "testIsSameLocalTime_Cal",
      "file": "src/test/java/org/apache/commons/lang3/time/DateUtilsTest.java",
      "method_start": 210, "method_end": 252,
      "method_source": "...完整测试方法...",
      "fail_line": 237,
      "fail_line_how": "message",
      "fail_line_code": "assertFalse(\"LANG-677\", DateUtils.isSameLocalTime(cal3, cal4));",
      "fail_statement": "...完整语句...",
      "exception": "junit.framework.AssertionFailedError: LANG-677",
      "trace_head": ["...前 6 行堆栈..."]
    }
  ],
  "prompt": "...渲染好的 prompt 文本..."
}
```

## 已知限制

- `fail_line_how = trace_only`（331 个测试）表示异常无消息、只能直接用堆栈行号，
  这类行号可能有 ±1~4 行漂移。
- MF bug 的 `buggy_functions` 有多个方法，`prompt` 会全部给出；若只想给一个，
  取 `buggy_lines` 中第一个方法即可。
- `approximate: true`（约 10 个方法）表示方法无法由行号精确定位，而是由补丁签名
  或堆栈帧推断。
