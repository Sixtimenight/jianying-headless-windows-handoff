# Windows native draft port

This private port reuses the plan validator, timeline builder, resource copying,
integrity checks and draft verifier from Jianying Headless. The macOS-only
filesystem bridge is replaced with a Windows adapter that calls the
`EncryptUtils` exports in the user's own `videoeditor.dll`. It does not copy or
modify Jianying's program files.

## Reviewed local profile

- Jianying Professional `11.5.0.14471`
- `videoeditor.dll` SHA-256:
  `37cadef37daff82e2ecdcd65080b9cbd7f6f9436f5e56c1ffb90c52c408eb7fb`
- Native metadata format: `new_version=187.0.0`, `version=360000`
- Home index: `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft\root_meta_info.json`
- Draft folder: the 草稿位置 setting (`currentCustomDraftPath` in
  `User Data\Config\globalSetting`), falling back to the index folder;
  `JY_DRAFT_ROOT` overrides it. `doctor` prints both `draft_root` and `index_root`.
- Timeline file: `draft_content.json` (macOS uses `draft_info.json`).
- `User Data` may be a junction (for example an isolated install kept on
  another drive). Creating files through the junction with CREATE_NEW
  returned EEXIST on the reference machine, so locks and the temporary index
  are written on the resolved directory.

The exported encrypt/decrypt function names were found in this installed DLL,
and the bridge compiles. On 2026-09-27 the codec decrypted a copy of a real
11.5.0 draft (`draft_content.json`, `draft_meta_info.json`), and
decrypt → encrypt → decrypt reproduced the plaintext byte for byte (ciphertext
differs per run, same length). A three-track plan (video with a speed change,
audio, two text segments) passed `build` and `verify-build`.
That draft was then published to the custom draft folder, opened and played in
Jianying 11.5.0.14471, saved (Jianying rewrote `draft_content.json`), reopened,
and passed `verify`. This covers video, audio and text only; other track types
and other machines are unverified. A
matching version string alone is not accepted: an updated DLL hash stops native
draft creation until reviewed.

## Build the local bridge

Use Python 3.9+ and MinGW-w64 `g++` with C++17. Jianying must be installed from
an authorized source.

```powershell
python tools\build_windows_codec.py
python skills\yichen-jianying-edit\scripts\headless_draft.py doctor
```

The bridge source retains the upstream `jy-draftc` MIT attribution in
`bridge\jy-draftc-windows.cpp` and `licenses\jy-draftc-MIT.txt`. The resulting
executable is local build output and is ignored by Git. `doctor` checks the
exact app version and DLL hash, the bridge, and the local draft index; it does
not read project contents or make a network request.

## Build and register a draft

Use a plan from `examples\basic.plan.json` or the plan reference. Replace its
media paths with absolute paths to local files you may use. The first Windows
port supports video, audio and text tracks. It rejects captured effects,
filters, masks, transitions, text effects and keyframes until those are
validated against Windows Jianying.

```powershell
$entry = 'skills\yichen-jianying-edit\scripts\headless_draft.py'
python $entry build --plan 'D:\path\to\plan.json' --out "$PWD\work\windows-build"
python $entry verify-build --build "$PWD\work\windows-build"
```

Build output is isolated under `work\windows-build\draft`; it does not change
the live Jianying project index. Before registration, save your current work
and fully exit Jianying:

```powershell
python $entry publish --build "$PWD\work\windows-build" --audit "$PWD\work\windows-publish-audit"
```

Registration creates a new draft directory and prepends one entry to the local
home index. It refuses name or ID conflicts, takes an exclusive local lock,
checks the index snapshot before replacement, and keeps an audit report. It
does not overwrite an existing draft. After registration, open Jianying and
check playback, text, timing, audio and media links. Save, exit fully, reopen,
and run `verify` before treating the draft as accepted.

## Headless native export

`export` on Windows runs `tools\build\jy-export-windows.exe`, the MSVC port of
`engine/native_export.cpp` (source: `bridge\jy-export-windows.cpp`). It needs
Visual Studio 2022 Build Tools with the C++ workload, because the engine passes
MSVC `std::string`, `std::shared_ptr` and `std::function` across its interface.

```powershell
python tools\build_windows_export.py
python skills\yichen-jianying-edit\scripts\headless_draft.py export --build "$PWD\work\windows-build" --out "$PWD\work\windows-export"
```

The flow matches macOS: `ProjectClient::init` → `DraftService/draftInit` →
`restoreDraft` (waits for the engine's restore-complete log line) →
`ExportService/exportStart` (waits for `export_callback: VE_INFO_COMPILE_DONE`),
then ffprobe frame/duration checks and a full decode. `--backend windows-ffmpeg`
still selects the separate FFmpeg path.

Reviewed ABI for `videoeditor.dll` 11.5.0.14471 (SHA-256 above). Exported
functions are resolved by their MSVC names; two internal functions are pinned:

| Item | Value |
| --- | --- |
| `make_shared<ExportStartReqStruct>()` | RVA `0x11b3ac0` |
| `restoreDraft(int sid, bool, int tid)` | RVA `0x11c41a0` |
| ReqStruct base | size `0x58`; tid `0x48` (int) |
| InitReqStruct / DraftInitReqStruct | size `0xb0` / `0x98` (same field order as macOS) |
| RespStruct | tid `0x48`, status code `0x4c` |
| ExportStartReqStruct | output path `0x58`, packed ExportConfig `0x78` |
| ExportConfig (packed) | width `0x47`, height `0x4b`, hw-encode `0x4f`, fps `0x52` (double), bitrate `0x66` |

The ExportConfig offsets are the macOS ones plus 8 (MSVC `std::string` is 32
bytes); they match what the engine's own `ClipVideoFunction::ClipStart` writes.
The macOS MP4-writer flag (`0x279`) is not set: the engine's own export path
leaves it at its default.

Verified on 2026-09-27: the three-track smoke draft exported headlessly as
H.264/AAC 1280×720, 30 fps, 90/90 frames, full decode; frames show both
subtitles and the 2× speed segment, and the 0.5-volume segment is ~2.7 dB lower.
There is no `sandbox-exec` equivalent; the helper runs as a separate process.

## Current limits

- Windows is enabled only for the local `11.5.0.14471` DLL fingerprint above.
- Mac-captured resource packages are not used by this backend.
- Existing-draft editing (`edit inspect/build/publish/verify`): on 2026-09-27 a
  real 54-minute, 244-clip 11.5 draft was inspected, trimmed to 5 clips with a
  track rename and a 0.5 volume change (241 operations), built, exported and
  published as a new draft; the source draft was unchanged. Native open/save
  and `edit verify` are pending. Windows paths are compared spelling-agnostic
  (`D:/a/b`, `D:\a\b` and mixed forms all occur in real drafts).
- Export stages external media recorded in an edit build's
  `media_dependencies` into `external-media/` and checks its SHA-256; other
  external files are still refused. The parameterless empty
  `sound_channel_mappings` node (`type: ""`) that Windows 11.5 writes on
  ordinary segments is accepted; mappings with parameters are still refused.
  Real-draft export: 1920×1080, 1207/1207 frames, full decode, 50 s; the 0.5
  volume clip measured 5.9 dB below the unity clip's source-relative level.
- Older schemas `164.0.0` / `181.0.0` (11.0–11.2 saves) and `183.0.0` (11.3)
  are accepted for the Windows 11.5 profile only. The engine rendered a 183 and
  a 164 draft headlessly with full frame counts once `##_draftpath_placeholder_`
  paths were resolved (the export stager does this). 11.5 upgrades them to 187
  on save; `edit verify` accepts that upgrade and, only during it, these
  observed migrations (each reported in the verify output):
  - repeated `text_effect` refs dropped when the same segment still references a
    `text_effect` with the same `resource_id`;
  - video/audio material durations re-snapped by less than one frame;
  - downloaded music relinked to `User Data\Cache\music` (byte-identical file);
  - another machine's `C:/Users/<other>/…/User Data/Cache/…` and
    `…/JianyingPro/[Apps/]<version>/Resources/…` paths relinked to this machine,
    only where the local file exists;
  - the previous save application may be 11.0.0–11.3.0.
  Verified 2026-09-28 after native save: a 244-clip 11.5 draft trimmed to 5
  clips (187) and a cloud-downloaded 11.3 ad draft (183); a cloud-downloaded
  11.1 ad draft (164 → 187) passes every content check, but its final
  source-unchanged check fails because the original was opened in Jianying
  after the build.
- Drafts downloaded from Jianying cloud keep `draft_content.json` and
  `template-2.tmp` as plain JSON while `Timelines/` holds encrypted copies; plain
  metadata is read directly and mirrors are compared by decoded content. They
  also carry a cloud entry identity: `inspect` reports it, and `edit build`
  requires `"cloud_identity": "detach"` in the plan, which clears the entry,
  space and user IDs on the copy only (purchase/rights fields untouched).
- Identical duplicate material nodes (repeated `text_effect` entries in older
  saves) are tolerated; list items with empty IDs are compared in order.
- Headless export still refuses filter/text-effect (花字) resources, as
  upstream: drafts carry no entitlement data, and headless rendering would skip
  Jianying's own membership check. Export such drafts from the Jianying UI.
- `remove_segment` also drops the material nodes that only the removed segment
  referenced, matching what the editor keeps on save.
- Compound (nested) drafts: inspect/build are refused on Windows until a
  Windows compound profile is reviewed.
- Headless export is verified on video, audio and text only; masks, effects,
  transitions and nested timelines are not yet exercised on Windows.
- A successful metadata round-trip proves the local codec call, not that the
  Windows editor accepts the generated project. The native UI acceptance is
  pending.
- This source comes from a personal, non-commercial upstream license. Keep the
  upstream `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md` and all third-party
  notices with this private fork. Do not publish, package or use it for
  commercial work without the written permissions described in those notices.
