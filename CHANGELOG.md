# Changelog

## Native tool development

- Add Blender-native selection/transform panels and public consumer panel registration.
- Build and verify a reproducible Blender 4.2+ extension ZIP without Core dependencies.
- Expire old dispatchers across file loads and discard callbacks even if timer removal fails.
- Retain registration resources when unregister fails so cleanup can retry.

## 0.1.0.dev0 — experimental source candidate

- Blender main-thread timer queue and bounded host dispatch
- Sidebar launcher, add-on registration, file-load/reload cleanup and session ownership
- Explicit Core contract checks and fail-closed unsupported platform paths
- Source/package CI and a real Blender addon-host probe

The reviewed adapter source is 79425d579c127324b797113f6ffd985351c52569.
The adapter passed 17 unit checks and 21 actual addon-host checks. A native WebView
was not created. The required Core changes are proposed in Core PR #497; there
is no released compatible dependency version or native support certification.
