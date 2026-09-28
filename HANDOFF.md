# 交接说明：Jianying Headless Windows 原生移植

本目录是 [mcncarl/jianying-headless](https://github.com/mcncarl/jianying-headless)（作者：逸尘）的
Windows 原生移植，已整理为本地 git 仓库，**尚未设置远程地址，也未推送**。

## 发布前必须确认

上游采用个人、非商业许可（见 `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.md`）：
未经其中所述的书面许可，不得发布、打包或用于商业用途。

- 推送到任何公开仓库之前，先取得逸尘的书面同意，并确认他希望的方式
  （向上游提 PR，或在独立仓库发布）。
- 保留上游全部许可与第三方声明文件，不要删改。
- `bridge/jy-draftc-windows.cpp` 来自 MIT 许可的 jy-draftc，保留 `licenses/jy-draftc-MIT.txt`。

## 这次移植新增 / 修改了什么

完整技术说明、逆向得到的 ABI、验收记录与已知限制见 `docs/windows-native.md`。

| 方面 | 内容 |
| --- | --- |
| 草稿位置 | 首页索引（`%LOCALAPPDATA%`）与草稿目录（剪映“草稿位置”设置）分开处理；`User Data` 为 junction 时在解析后的目录上加锁 |
| 时间线文件 | Windows 使用 `draft_content.json`（macOS 为 `draft_info.json`） |
| 明文草稿 | 6.0 之前及云端下载的草稿可能是明文 JSON，直接读取 |
| 无界面导出 | 新增 `bridge/jy-export-windows.cpp`（MSVC）与 `tools/build_windows_export.py`；`export` 在 Windows 默认走原生引擎 |
| 编辑副本 | 放开 Windows 上的 `edit`；路径比较兼容 `/`、`\` 与混合写法；`remove_segment` 同步清理孤立素材 |
| 老格式 | Windows 11.5 profile 接受 164 / 181 / 183，并只认可升级到 187 时观察到的几类迁移 |
| 云端工程 | `inspect` 报告云端身份；`build` 需计划显式 `"cloud_identity": "detach"` |
| 导出素材 | 编辑构建中已记录哈希的外部素材会复制进任务目录校验；空的默认声道映射放行 |

未改变的上游边界：含滤镜 / 花字资源的草稿仍拒绝无界面导出；复合片段编辑仍拒绝；
不读取账号凭证，不调用剪映私有云端接口。

## 构建与自检（Windows）

```powershell
python tools\build_windows_codec.py        # MinGW-w64 g++，编解码桥
python tools\build_windows_export.py       # VS 2022 Build Tools，导出桥
python skills\yichen-jianying-edit\scripts\headless_draft.py doctor
```

`tools/build/` 为本地构建产物，已在 `.gitignore` 中排除。

## 未随仓库提交的内容

- `work/`：本机验收使用的真实草稿检查结果、构建与审计记录（含个人路径与素材信息）。
- 编译产物、剪映程序文件、草稿与媒体文件（见 `.gitignore`）。

## 已知测试状态

仓库自带测试中，部分依赖 macOS 专用夹具或 Unix 模块（`fcntl`），在 Windows 上报错；
这些失败在移植前即存在，移植没有引入新的失败。
