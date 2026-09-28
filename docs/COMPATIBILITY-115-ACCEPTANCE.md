# 11.5 兼容补丁的验收状态与复验步骤

2026-09-22 更新。当前状态如下；本机 codec 的通过结果不能改称上游固定环境通过。

| 项目 | 状态 |
| --- | --- |
| 普通、1.5 倍速、平直／非平直自定义曲线的独立副本 | 已完成打开、播放、保存、退出、冷重开及完整回读；来源未变、四镜像一致 |
| 真实保存差异与负向回放 | 原生自身复制复现平均速度重算；限定修复后原失败现场及冷重开通过，真实数据副本的 30 项双向回放符合预期 |
| 短片曲线平均值疑点 | 原生平均值对照的 189 个时间采样和两份 90 帧输出一致，已移出待办；改变曲线的对照检出变化，但其 89／90 帧输出仍拒收 |
| 裁剪／固定变速与 X 动画 | 八种组合的原生诊断实验通过；自动／显式 Y 逐帧位置一致，生成器限制及 UI 交付边界不变 |
| 普通音频 1×／1.5× | 独立副本保存、完全退出、冷重开、播放、速度面板及两次完整回读通过 |
| mode=1 null／empty 缺速 | 原生“无”与“重置”未产生这两种形态；已收窄实现，缺速双向拒绝，不再声称有默认兼容 |
| 上游固定 codec 同输入 A/B | 未完成：缺少匹配二进制和精确工具链，现有本机 codec 不能替代 |

原生保存见[视频证据](evidence/native-save-115.json)、[音频及空曲线边界](evidence/audio-save-115.json)及[实现边界](SAVED-DEFAULTS-115.md)，
短片曲线平均值见[原生对照](evidence/curve-average-native-115.json)，时间映射见[实验摘要](evidence/time-mapping-115.json)。黑屏和 EOS 的正式修复另行处理，
不并入本次提交。

## 固定 codec 的同输入 A/B

准备两个独立检出：基线 `344a78f179dc275d76fc39aaaaa27d325254c156` 与最终候选。
记录两份源码 SHA／未提交 diff、Python、ffmpeg/ffprobe、doctor 输出和工具链信息。
两边均须使用未经改动的精确配置，固定 codec SHA 为
`b6533eb5eb1eea58dfa74fb1d16d3bb580970fe881f587605d358af1745f971d`，
11.5 `libvideoeditor.dylib` SHA 为
`2041482a1aaeffa4d8bd69b836f8cf38807aaad8021bca410d567c59af3bccfa`。
缺组件时先按项目构建说明处理；doctor 未通过就记录环境缺口，不替换 pin。

下列绝对路径为占位符，替换为实际两个检出和**尚不存在**的新任务目录。命令不登记首页。

```sh
unset JIANYING_HEADLESS_ROOT PYTHONPATH
PR10_BASE_ROOT=/absolute/path/to/base-checkout
PR10_CANDIDATE_ROOT=/absolute/path/to/candidate-checkout
PR10_WORK=/absolute/path/to/new-validation-job

python3 "$PR10_BASE_ROOT/skills/yichen-jianying-edit/scripts/headless_draft.py" doctor
python3 "$PR10_CANDIDATE_ROOT/skills/yichen-jianying-edit/scripts/headless_draft.py" doctor
mkdir "$PR10_WORK"
python3 "$PR10_CANDIDATE_ROOT/tools/reproduce_position_115.py" generate --out "$PR10_WORK/fixture"
python3 "$PR10_BASE_ROOT/skills/yichen-jianying-edit/scripts/headless_draft.py" build \
  --plan "$PR10_WORK/fixture/a-x-only.plan.json" --out "$PR10_WORK/frozen-a"
python3 "$PR10_BASE_ROOT/skills/yichen-jianying-edit/scripts/headless_draft.py" verify-build --build "$PR10_WORK/frozen-a"
python3 "$PR10_CANDIDATE_ROOT/skills/yichen-jianying-edit/scripts/headless_draft.py" verify-build --build "$PR10_WORK/frozen-a"
python3 "$PR10_BASE_ROOT/skills/yichen-jianying-edit/scripts/headless_draft.py" export \
  --build "$PR10_WORK/frozen-a" --out "$PR10_WORK/base-export"
python3 "$PR10_CANDIDATE_ROOT/skills/yichen-jianying-edit/scripts/headless_draft.py" export \
  --build "$PR10_WORK/frozen-a" --out "$PR10_WORK/candidate-export"
python3 "$PR10_CANDIDATE_ROOT/tools/reproduce_position_115.py" measure \
  --video "$PR10_WORK/base-export/render.mp4" --out "$PR10_WORK/base-measurement.json"
python3 "$PR10_CANDIDATE_ROOT/tools/reproduce_position_115.py" measure \
  --video "$PR10_WORK/candidate-export/render.mp4" --out "$PR10_WORK/candidate-measurement.json"
```

逐步执行并查看退出状态。基线测量预期因 Y 抖动返回 1，必须确认失败项仅为 Y 稳定性，
不能将任意失败当成复现；候选测量应返回 0。任一原生导出失败时保留现场并停止该次验收，
不覆盖或重试到成功。

关闭条件：两份均完整解码 60 帧、输入 build 未变；基线出现既有 Y 抖动，候选 Y 跨度
不超过 1 像素；比较两份测量 JSON 的全部 60 个 `frames` 条目，X 中心逐帧一致。
若基线不再复现，记录事实并查运行差异，不更改阈值。保留计划、doctor、build manifest、
两份导出 result、完整测量、源码身份和命令日志。现有字节不同的本机 codec 证据单独标注。

## 真实保存证据

本轮已完成上述四种视频上下文及普通／1.5× 音频；下面保留复验步骤。普通草稿的历史冷重开不能代替曲线场次。
使用独立的原生有效合成草稿，
通过剪映实际支持的速度操作形成待验证上下文；读取真实材料与片段引用，记录实际
`mode`／`curve_speed`，不靠手工填字段就假定对应某种 UI 语义。

按 `edit inspect → edit build → edit verify-build → edit publish` 创建独立副本，
只做改轨道名等与速度无关的编辑。登记前保存工作并正常退出剪映，不结束其它工作进程。
打开副本、检查速度内容、保存、完全退出、冷启动重开再退出后，运行：

```sh
python3 skills/yichen-jianying-edit/scripts/headless_draft.py edit verify \
  --build /absolute/path/to/edit-build --report /absolute/path/to/new-save-report.json
```

应保留：来源 manifest、冻结 expected、真实保存 actual、所选材料与片段引用、用户或
操作者的播放／保存／冷重开记录，以及四镜像一致、来源未变、完整回读报告。
回读工具自身的 `verified` 不能代替实际 UI 操作记录。某个模式无法由支持流程建立时，
检查实现是否应收窄，并明确记录不再接受的输入；解析实验不作为替代。本候选已据此拒绝未验证曲线形态的缺速默认解释。

使用原保存数据的独立 JSON 副本做非默认 1.5、新旧比较方向、材料引用改变及曲线内容
改变的负向回放，不修改已登记草稿来制造反例。若真实保存暴露了比较错误，修正对应责任
位置；若暂时没有该证据，保持审阅问题未关闭，不提前宣布扩大范围已获验证。

## 裁剪／固定变速与 X 动画

9/22 的独立研究从生产构造器接受的无动画 build 出发，先通过精确 11.5 原生时间函数
取得偏移，再加入动画并冻结输入。官方解析、恢复和实际输出核实时间范围、速度、X 点和源帧进度。
普通、裁剪、2×、0.5×、裁剪＋2×、裁剪＋0.5×、1.5×、裁剪＋1.5× 八种组合均通过。
首轮两个正向输出各 360 帧，小数速度联合对照 240 帧；自动／显式 Y 的逐帧 X/Y 差为 0，
X 最大误差约 0.744 像素，Y 跨度 0。旧时间假设负对照的 360 帧仍完整，但五种运动错误被检出。

本补交复核了研究证据包的 115 个文件及 1,320 行帧表，并确认导出 Python 算法与
C++ 源码一致；仅更正“偏移为片段局部时间”的注释。已有手工 120 帧的旧结论保留为历史。
这些是原生时间语义已经核实的诊断输入，不是 UI 保存出的导入草稿，也不是公开生成器交付能力。
生成器仍拒绝裁剪／变速与关键帧组合；曲线、嵌套内部和其它通道没有因此通过。
