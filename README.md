# 全球影视发行日报数据

此仓库用于给 iOS App 提供公开的静态 JSON 片单。`releases.json` 是从当前 App 内置样板导出的初始快照，不是自动采集结果；公开之前请核实发行日期、简介、来源与评分。

## App 接口

GitHub Pages 启用后，接口地址为：

`https://hx614.github.io/global-film-release-feed/releases.json`

在 iPhone App 的“日报”页点“设置”，粘贴该地址；之后 App 会在启动时获取，并可手动刷新。

## 更新数据

编辑 `releases.json`，保持 `updatedAt`、`films`、`filmAreas`、`mediaKinds`、`contentSources` 和 `crossSources` 数据结构。提交到 `main` 后 Pages 会发布变更。当前仓库不含自动搜集或事实核验脚本，改动此文件仍需人工整理并提交。

内容会公开在互联网上。不要放 API key、私人资料或需要登录才能访问的内容。
