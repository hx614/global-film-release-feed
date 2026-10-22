# 全球影视发行日报数据

此公开仓库同时托管 iOS App 的 JSON 数据源和每日更新工作流。App 地址：

`https://hx614.github.io/global-film-release-feed/releases.json`

## 每日自动更新

GitHub Actions 预定每天北京时间 01:10 运行，也可以在仓库 Actions 页面手动触发。工作流会：

- 从 TMDB 获取未来 90 天与过去 7 天的电影院线、数字发行和实体碟档期，并获取简介、海报及 TMDB 评分。
- 从 TVmaze 获取过去 7 天至未来 30 天的流媒体单集排期。
- 按影片制作地区归入大陆、港澳台、欧美、日本、韩国或其他地区，然后生成完整快照并提交 `releases.json`。
- App 在启动或手动刷新时读取同一个 JSON 地址。

### 一次性设置 TMDB 凭据

TMDB 的非商业开发 API 需要账户的 API Read Access Token。拿到后在仓库打开 **Settings → Secrets and variables → Actions → New repository secret**，名称填 `TMDB_READ_ACCESS_TOKEN`，值粘贴 token。不要把 token 写进代码、JSON 或聊天消息。

当前工作流在 Secret 缺失时会停止，不会覆盖正在使用的数据。设置 Secret 后，可在 **Actions → Update daily film release feed → Run workflow** 手动运行一次；此后会按计划运行。

## 数据范围和准确性

这些是开放片目数据库，不等于发行方的实时公告：数据库可能漏项、地区标注不全或延迟，平台和发行日期也可能临时调整。剧集部分按 TVmaze 已登记的流媒体单集排期展示，未收录的剧集不会出现。影片按上映地区与类型展示；平台数字发行不一定能精确到具体流媒体服务商。发布后仍建议对重要档期点开来源复核。

本产品使用 TMDB API，但不受 TMDB 背书或认证。TVmaze 的单集来源链接附在每条剧集记录上。TMDB 和 TVmaze 的资料分别遵从其服务条款及归属要求。

## 数据结构

工作流生成的完整快照包含 `updatedAt`、`completeSnapshot`、`films`、`filmAreas`、`mediaKinds`、`contentSources`、`crossSources` 和 `attribution`。更新会替换旧快照，避免已变更或过期档期永久留在 App 里。

内容会公开在互联网上。不要提交 API token、私人资料或需要登录才能访问的内容。
